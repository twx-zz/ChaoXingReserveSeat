import json
import time
import argparse
import os
import logging
import datetime
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

from utils import reserve, get_user_credentials

get_current_time = lambda action: (
    (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%H:%M:%S")
    if action
    else time.strftime("%H:%M:%S", time.localtime())
)
get_current_dayofweek = lambda action: (
    (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%A")
    if action
    else time.strftime("%A", time.localtime())
)

SLEEPTIME = 0.1  # 减少间隔时间
RESERVE_TARGET_TIME = "16:35:00"  # 预约开始的目标时间（北京时间）
ENABLE_SLIDER = True  # 是否有滑块验证
MAX_ATTEMPT = 1  # 减少重试次数，专注速度
RESERVE_NEXT_DAY = False  # 预约明天而不是今天的
CAPTCHA_POOL_SIZE = 3  # 验证码池大小，减少避免过期
CAPTCHA_PRELOAD_TIME = 3  # 提前3秒开始预加载验证码，避免过期

class CaptchaPool:
    """验证码缓存池 - 优化版本，防止过期"""
    def __init__(self, session, pool_size=CAPTCHA_POOL_SIZE):
        self.session = session
        self.pool_size = pool_size
        self.captcha_queue = Queue()
        self.is_active = True
        self.lock = threading.Lock()
        self.last_refresh_time = time.time()
        
    def start_preloading(self):
        """开始预加载验证码"""
        logging.info(f"🔄 开始预加载验证码池，目标数量: {self.pool_size}")
        
        def preload_worker():
            while self.is_active and self.captcha_queue.qsize() < self.pool_size:
                try:
                    # 刷新session状态，防止过期
                    self.session.refresh_session()
                    
                    captcha = self.session.resolve_captcha()
                    if captcha:
                        self.captcha_queue.put((captcha, time.time()))
                        logging.info(f"✅ 验证码预加载成功，当前池大小: {self.captcha_queue.qsize()}")
                    time.sleep(0.3)  # 避免请求过快
                except Exception as e:
                    logging.warning(f"⚠️ 验证码预加载失败: {e}")
                    time.sleep(0.5)
        
        # 启动预加载线程
        thread = threading.Thread(target=preload_worker, daemon=True)
        thread.start()
    
    def get_fresh_captcha(self):
        """获取新鲜的验证码（实时生成）"""
        try:
            # 刷新session状态
            self.session.refresh_session()
            return self.session.resolve_captcha()
        except Exception as e:
            logging.error(f"💥 获取新鲜验证码失败: {e}")
            return ""
    
    def get_captcha(self):
        """获取一个验证码 - 优先使用新鲜验证码"""
        # 直接获取新鲜验证码，避免使用可能过期的池中验证码
        return self.get_fresh_captcha()
    
    def stop(self):
        """停止预加载"""
        self.is_active = False

def refresh_session_status(session, roomid, seatid):
    """刷新Session状态，防止过期"""
    try:
        # 重新访问座位页面，刷新session状态
        session.requests.get(
            f"https://office.chaoxing.com/front/third/apps/seat/code?id={roomid}&seatNum={seatid}", 
            verify=False
        )
        # 访问其他关键页面保持活跃
        session.requests.get("https://office.chaoxing.com/data/apps/seat/getusedtimes", verify=False)
        logging.info("🔄 Session状态已刷新")
        return True
    except Exception as e:
        logging.warning(f"⚠️ Session状态刷新失败: {e}")
        return False

def warm_up_session(session, roomid, seatid):
    """Session预热 - 模拟正常浏览行为"""
    try:
        logging.info("🔥 开始Session预热...")
        # 访问座位页面
        session.requests.get(
            f"https://office.chaoxing.com/front/third/apps/seat/code?id={roomid}&seatNum={seatid[0]}", 
            verify=False
        )
        time.sleep(0.5)
        
        # 访问其他相关页面
        session.requests.get("https://office.chaoxing.com/data/apps/seat/getusedtimes", verify=False)
        time.sleep(0.5)
        
        logging.info("✅ Session预热完成")
    except Exception as e:
        logging.warning(f"⚠️ Session预热失败: {e}")

def rapid_submit_single_enhanced(session, times, roomid, seatid, captcha_pool, action):
    """增强版极速提交单个预约 - 防止session过期"""
    start_time = time.time()
    
    try:
        # 1. 刷新session状态，防止过期
        refresh_session_status(session, roomid, seatid)
        
        # 2. 获取新鲜验证码（实时生成，避免过期）
        captcha = captcha_pool.get_fresh_captcha()
        captcha_time = time.time()
        
        # 3. 立即获取token
        token, value = session._get_page_token(
            session.url.format(roomid, seatid), require_value=True
        )
        token_time = time.time()
        logging.info(f"⚡ 快速获取token: {token} (耗时: {token_time - captcha_time:.2f}s)")
        
        # 4. 立即提交
        success = session.get_submit(
            session.submit_url,
            times=times,
            token=token,
            roomid=roomid,
            seatid=seatid,
            captcha=captcha,
            action=action,
            value=value,
        )
        
        total_time = time.time() - start_time
        if success:
            logging.info(f"🎉 预约成功！总耗时: {total_time:.2f}s")
        else:
            logging.warning(f"❌ 预约失败，总耗时: {total_time:.2f}s")
            
        return success
        
    except Exception as e:
        total_time = time.time() - start_time
        logging.error(f"💥 预约异常: {e}，总耗时: {total_time:.2f}s")
        return False

def pre_login_users(users, usernames, passwords, action):
    """提前登录所有用户并预热"""
    logged_sessions = []
    current_dayofweek = get_current_dayofweek(action)
    
    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()
        if action:
            username, password = (
                usernames.split(",")[index],
                passwords.split(",")[index],
            )
        
        if current_dayofweek not in daysofweek:
            logging.info("Today not set to reserve")
            logged_sessions.append(None)
            continue
            
        logging.info(f"User {username}: 提前登录中...")
        
        # 创建预约会话并登录
        s = reserve(
            sleep_time=SLEEPTIME,
            max_attempt=MAX_ATTEMPT,
            enable_slider=ENABLE_SLIDER,
            reserve_next_day=RESERVE_NEXT_DAY,
        )
        s.get_login_status()
        login_success, msg = s.login(username, password)
        
        if login_success:
            s.requests.headers.update({"Host": "office.chaoxing.com"})
            # Session预热
            warm_up_session(s, roomid, seatid)
            logged_sessions.append(s)
        else:
            logging.error(f"User {username} login failed: {msg}")
            logged_sessions.append(None)
    
    return logged_sessions

def wait_for_target_time_minimal_preload(target_time, action, logged_sessions):
    """等待到达目标时间，最小化预加载时间"""
    current_time = get_current_time(action)
    
    if current_time < target_time:
        # 获取当前北京时间
        if action:
            current_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        else:
            current_dt = datetime.datetime.now()
            
        # 计算今天的目标时间
        target_dt = current_dt.replace(
            hour=int(target_time.split(":")[0]),
            minute=int(target_time.split(":")[1]),
            second=int(target_time.split(":")[2]),
            microsecond=0
        )
        
        # 如果目标时间已过，则设为明天
        if target_dt <= current_dt:
            target_dt += datetime.timedelta(days=1)
        
        wait_seconds = (target_dt - current_dt).total_seconds()
        
        # 如果等待时间大于预加载时间，先等待到预加载时机
        if wait_seconds > CAPTCHA_PRELOAD_TIME:
            wait_until_preload = wait_seconds - CAPTCHA_PRELOAD_TIME
            logging.info(f"距离目标时间 {target_time}（北京时间）还有 {wait_seconds:.1f} 秒，先等待 {wait_until_preload:.1f} 秒...")
            time.sleep(wait_until_preload)
            
            # 开始最小化预加载
            logging.info(f"🔄 开始最小化预加载，提前 {CAPTCHA_PRELOAD_TIME} 秒")
            captcha_pools = []
            for s in logged_sessions:
                if s is not None:
                    captcha_pool = CaptchaPool(s, 1)  # 池大小设为1，避免过期
                    captcha_pools.append(captcha_pool)
                else:
                    captcha_pools.append(None)
            
            # 等待剩余时间
            time.sleep(CAPTCHA_PRELOAD_TIME)
        else:
            # 如果等待时间很短，立即创建池
            logging.info(f"🔄 立即创建验证码池")
            captcha_pools = []
            for s in logged_sessions:
                if s is not None:
                    captcha_pool = CaptchaPool(s, 1)
                    captcha_pools.append(captcha_pool)
                else:
                    captcha_pools.append(None)
            time.sleep(wait_seconds)
    else:
        # 目标时间已过，立即创建验证码池
        logging.info(f"🔄 目标时间已过，立即创建验证码池")
        captcha_pools = []
        for s in logged_sessions:
            if s is not None:
                captcha_pool = CaptchaPool(s, 1)
                captcha_pools.append(captcha_pool)
            else:
                captcha_pools.append(None)
    
    logging.info(f"到达目标时间 {target_time}（北京时间），开始预约")
    return captcha_pools

def start_reservation_enhanced(users, logged_sessions, captcha_pools, action):
    """增强版预约开始 - 防止session过期"""
    current_dayofweek = get_current_dayofweek(action)
    
    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()
        
        if current_dayofweek not in daysofweek:
            continue
            
        s = logged_sessions[index]
        captcha_pool = captcha_pools[index]
        if s is None or captcha_pool is None:
            continue
        
        logging.info(f"🚀 开始极速预约 - 用户 {username}")
        
        # 处理时间段
        time_slots = times if isinstance(times[0], list) else [times]
        
        # 记录成功预约的时间段
        successful_reservations = []
        
        # 尝试预约所有时间段
        for i, time_slot in enumerate(time_slots):
            logging.info(f"⚡ 预约时间段 {i+1}/{len(time_slots)}: {time_slot}")
            
            success = rapid_submit_single_enhanced(
                s, time_slot, roomid, seatid[0], captcha_pool, action
            )
            
            if success:
                successful_reservations.append(time_slot)
                logging.info(f"✅ 时间段 {time_slot} 预约成功！")
            else:
                logging.warning(f"❌ 时间段 {time_slot} 预约失败")
            
            # 短暂间隔，避免限流
            if i < len(time_slots) - 1:
                time.sleep(0.2)
        
        # 输出最终结果
        if successful_reservations:
            logging.info(f"🎊 用户 {username} 预约汇总：成功预约了 {len(successful_reservations)} 个时间段: {successful_reservations}")
        else:
            logging.warning(f"😞 用户 {username} 预约汇总：所有时间段预约均失败")
        
        # 停止验证码池
        captcha_pool.stop()

def main(users, action=False):
    current_time = get_current_time(action)
    logging.info(f"程序启动，立即登录 (action={'on' if action else 'off'})")
    
    usernames, passwords = None, None
    if action:
        usernames, passwords = get_user_credentials(action)
    
    # 提前登录所有用户并预热
    logged_sessions = pre_login_users(users, usernames, passwords, action)
    
    # 等待目标时间，最小化预加载避免过期
    captcha_pools = wait_for_target_time_minimal_preload(RESERVE_TARGET_TIME, action, logged_sessions)
    
    # 开始增强版极速预约
    start_reservation_enhanced(users, logged_sessions, captcha_pools, action)

def debug(users, action=False):
    logging.info(
        f"Global settings: \nSLEEPTIME: {SLEEPTIME}\nRESERVE_TARGET_TIME: {RESERVE_TARGET_TIME}\nENABLE_SLIDER: {ENABLE_SLIDER}\nRESERVE_NEXT_DAY: {RESERVE_NEXT_DAY}\nCAPTCHA_POOL_SIZE: {CAPTCHA_POOL_SIZE}\nCAPTCHA_PRELOAD_TIME: {CAPTCHA_PRELOAD_TIME}"
    )
    logging.info(f"Debug Mode start! , action {'on' if action else 'off'}")
    
    if action:
        usernames, passwords = get_user_credentials(action)
    
    current_dayofweek = get_current_dayofweek(action)
    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()
        if type(seatid) == str:
            seatid = [seatid]
        if action:
            username, password = (
                usernames.split(",")[index],
                passwords.split(",")[index],
            )
        if current_dayofweek not in daysofweek:
            logging.info("Today not set to reserve")
            continue
        logging.info(f"----------- {username} -- {times} -- {seatid} try -----------")
        s = reserve(
            sleep_time=SLEEPTIME,
            max_attempt=MAX_ATTEMPT,
            enable_slider=ENABLE_SLIDER,
            reserve_next_day=RESERVE_NEXT_DAY,
        )
        s.get_login_status()
        s.login(username, password)
        s.requests.headers.update({"Host": "office.chaoxing.com"})
        
        # 预热并测试增强版极速提交
        captcha_pool = CaptchaPool(s, 1)
        
        # 测试所有时间段的快速预约
        successful_debug_reservations = []
        if isinstance(times[0], list):
            for i, time_slot in enumerate(times):
                success = rapid_submit_single_enhanced(s, time_slot, roomid, seatid[0], captcha_pool, action)
                if success:
                    successful_debug_reservations.append(time_slot)
                if i < len(times) - 1:
                    time.sleep(0.3)
        else:
            success = rapid_submit_single_enhanced(s, times, roomid, seatid[0], captcha_pool, action)
            if success:
                successful_debug_reservations.append(times)
        
        logging.info(f"🔍 调试模式预约汇总：成功预约了 {len(successful_debug_reservations)} 个时间段: {successful_debug_reservations}")
        
        captcha_pool.stop()
        return

def get_roomid(args1, args2):
    username = input("请输入用户名：")
    password = input("请输入密码：")
    s = reserve(
        sleep_time=SLEEPTIME,
        max_attempt=MAX_ATTEMPT,
        enable_slider=ENABLE_SLIDER,
        reserve_next_day=RESERVE_NEXT_DAY,
    )
    s.get_login_status()
    s.login(username=username, password=password)
    s.requests.headers.update({"Host": "office.chaoxing.com"})
    encode = input("请输入deptldEnc：")
    s.roomid(encode)

if __name__ == "__main__":
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    parser = argparse.ArgumentParser(prog="Chao Xing seat auto reserve")
    parser.add_argument("-u", "--user", default=config_path, help="user config file")
    parser.add_argument(
        "-m",
        "--method",
        default="reserve",
        choices=["reserve", "debug", "room"],
        help="for debug",
    )
    parser.add_argument(
        "-a",
        "--action",
        action="store_true",
        help="use --action to enable in github action",
    )
    args = parser.parse_args()
    func_dict = {"reserve": main, "debug": debug, "room": get_roomid}
    with open(args.user, "r+") as data:
        usersdata = json.load(data)["reserve"]
    func_dict[args.method](usersdata, args.action)
