import json
import time
import argparse
import os
import logging
import datetime
import threading
import requests
import requests.adapters
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
import random

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

# ================= 晚高峰专用参数 =================
SLEEPTIME = 0.02  # 极短间隔
RESERVE_TARGET_TIME = "22:00:00"  
ENABLE_SLIDER = True  
MAX_ATTEMPT = 1  # 只试一次，避免token过期
RESERVE_NEXT_DAY = True
CAPTCHA_POOL_SIZE = 8  # 增大验证码池应对高峰
CAPTCHA_PRELOAD_AT = "21:59:50"  # 提前10秒预加载
TOKEN_EXPIRE_TIME = 5  # 假设高峰期token只有5秒有效期
PEAK_HOUR_MODE = True  # 高峰期模式
# ==========================================

class PeakHourCaptchaPool:
    """高峰期专用验证码池 - 极速刷新"""
    def __init__(self, session, pool_size=CAPTCHA_POOL_SIZE):
        self.session = session
        self.pool_size = pool_size
        self.captcha_queue = Queue()
        self.is_active = True
        self.lock = threading.Lock()
        self.generation_count = 0
        
    def start_aggressive_preloading(self):
        """激进的验证码预加载 - 高峰期专用"""
        logging.info(f"🔥 启动高峰期验证码池，目标数量: {self.pool_size}")
        
        def aggressive_worker():
            while self.is_active:
                try:
                    current_size = self.captcha_queue.qsize()
                    
                    # 保持池子充满
                    if current_size < self.pool_size:
                        captcha = self.session.resolve_captcha()
                        if captcha:
                            self.captcha_queue.put({
                                'captcha': captcha,
                                'timestamp': time.time(),
                                'id': self.generation_count
                            })
                            self.generation_count += 1
                            logging.info(f"⚡ 验证码#{self.generation_count} 预加载成功，池大小: {current_size + 1}")
                    
                    # 更积极的清理策略 - 高峰期验证码有效期更短
                    self._aggressive_cleanup()
                    
                    # 更短的休息时间
                    time.sleep(0.1)
                    
                except Exception as e:
                    logging.warning(f"⚠️ 验证码生成失败: {e}")
                    time.sleep(0.2)
        
        # 启动多个worker线程
        for i in range(2):  # 2个并发生成线程
            thread = threading.Thread(target=aggressive_worker, daemon=True)
            thread.start()
    
    def _aggressive_cleanup(self):
        """激进清理过期验证码"""
        current_time = time.time()
        temp_queue = Queue()
        expired_count = 0
        
        while not self.captcha_queue.empty():
            captcha_data = self.captcha_queue.get()
            # 高峰期验证码有效期只有10秒
            if current_time - captcha_data['timestamp'] < 10:
                temp_queue.put(captcha_data)
            else:
                expired_count += 1
        
        # 放回未过期的
        while not temp_queue.empty():
            self.captcha_queue.put(temp_queue.get())
            
        if expired_count > 0:
            logging.info(f"🧹 清理了 {expired_count} 个过期验证码")
    
    def get_ultra_fresh_captcha(self):
        """获取超新鲜验证码 - 高峰期专用"""
        best_captcha = None
        newest_time = 0
        
        # 找到最新的验证码
        temp_captchas = []
        while not self.captcha_queue.empty():
            captcha_data = self.captcha_queue.get()
            temp_captchas.append(captcha_data)
            
            if captcha_data['timestamp'] > newest_time:
                newest_time = captcha_data['timestamp']
                best_captcha = captcha_data
        
        # 把其他验证码放回队列
        for captcha_data in temp_captchas:
            if captcha_data != best_captcha:
                self.captcha_queue.put(captcha_data)
        
        if best_captcha and time.time() - best_captcha['timestamp'] < 8:
            logging.info(f"🎯 使用最新验证码#{best_captcha['id']} (年龄: {time.time() - best_captcha['timestamp']:.1f}s)")
            return best_captcha['captcha']
        else:
            # 紧急生成新验证码
            logging.info("🚨 紧急生成新验证码")
            return self.session.resolve_captcha()
    
    def stop(self):
        self.is_active = False


def peak_hour_session_setup(session, roomid, seatid):
    """高峰期专用session设置"""
    try:
        logging.info("🏃‍♂️ 高峰期快速Session设置...")
        
        # 添加更多header模拟真实用户
        session.requests.headers.update({
            "Host": "office.chaoxing.com",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        })
        
        # 设置更短的超时时间
        session.requests.timeout = 2
        
        logging.info("✅ 高峰期Session设置完成")
    except Exception as e:
        logging.warning(f"⚠️ Session设置失败: {e}")


def ultimate_speed_submit(session, times, roomid, seatid, captcha_pool, action):
    """终极速度提交 - 专为高峰期设计"""
    start_time = time.time()
    
    try:
        # 并行获取验证码和token - 争取时间
        captcha_future = None
        token_future = None
        
        def get_captcha():
            return captcha_pool.get_ultra_fresh_captcha()
        
        def get_token():
            return session._get_page_token(
                session.url.format(roomid, seatid), require_value=True
            )
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            captcha_future = executor.submit(get_captcha)
            token_future = executor.submit(get_token)
            
            # 等待两个任务完成
            captcha = captcha_future.result(timeout=3)
            token, value = token_future.result(timeout=3)
        
        prep_time = time.time() - start_time
        logging.info(f"⚡ 并行准备完成 Token: {token[:10]}..., 耗时: {prep_time:.3f}s")
        
        # 立即提交，无任何延迟
        submit_start = time.time()
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
        submit_time = time.time() - submit_start
        total_time = time.time() - start_time
        
        if success:
            logging.info(f"🚀 终极速度提交成功！准备:{prep_time:.3f}s + 提交:{submit_time:.3f}s = 总计:{total_time:.3f}s")
        else:
            logging.warning(f"❌ 提交失败，总耗时: {total_time:.3f}s")
            
        return success
        
    except Exception as e:
        total_time = time.time() - start_time
        logging.error(f"💥 终极提交异常: {e}，总耗时: {total_time:.3f}s")
        return False


def staggered_login(users, usernames, passwords, action):
    """错峰登录 - 避免同时登录被限制"""
    logged_sessions = []
    captcha_pools = []
    current_dayofweek = get_current_dayofweek(action)
    
    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()
        if action:
            username, password = (
                usernames.split(",")[index],
                passwords.split(",")[index],
            )
        
        if current_dayofweek not in daysofweek:
            logged_sessions.append(None)
            captcha_pools.append(None)
            continue
        
        # 错峰登录，避免批量检测
        if index > 0:
            stagger_delay = random.uniform(0.5, 1.5)
            logging.info(f"错峰延迟 {stagger_delay:.1f} 秒...")
            time.sleep(stagger_delay)
            
        logging.info(f"User {username}: 高峰期登录中...")
        
        s = reserve(
            sleep_time=SLEEPTIME,
            max_attempt=MAX_ATTEMPT,
            enable_slider=ENABLE_SLIDER,
            reserve_next_day=RESERVE_NEXT_DAY,
        )
        s.get_login_status()
        login_success, msg = s.login(username, password)
        
        if login_success:
            logged_sessions.append(s)
            captcha_pools.append(PeakHourCaptchaPool(s, CAPTCHA_POOL_SIZE))
            logging.info(f"✅ {username} 登录成功")
        else:
            logging.error(f"❌ {username} 登录失败: {msg}")
            logged_sessions.append(None)
            captcha_pools.append(None)
    
    return logged_sessions, captcha_pools


def microsecond_timing_wait(target_time, action):
    """微秒级时间等待 - 高精度"""
    start_wait = time.time()
    
    while True:
        current_time = get_current_time(action)
        
        if current_time >= target_time:
            break
            
        if action:
            current_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        else:
            current_dt = datetime.datetime.now()
            
        target_dt = current_dt.replace(
            hour=int(target_time.split(":")[0]),
            minute=int(target_time.split(":")[1]),
            second=int(target_time.split(":")[2]),
            microsecond=0
        )
        
        if target_dt <= current_dt:
            target_dt += datetime.timedelta(days=1)
            
        wait_seconds = (target_dt - current_dt).total_seconds()
        
        if wait_seconds > 5:
            time.sleep(1)
        elif wait_seconds > 0.5:
            time.sleep(0.1)
        elif wait_seconds > 0.05:
            time.sleep(0.01)
        else:
            break
    
    total_wait = time.time() - start_wait
    logging.info(f"⏰ 微秒级等待完成，等待了 {total_wait:.3f} 秒")


def peak_hour_reservation(users, logged_sessions, captcha_pools, action):
    """高峰期抢座 - 终极优化版"""
    current_dayofweek = get_current_dayofweek(action)
    
    # 预先准备所有session
    active_sessions = []
    for index, user in enumerate(users):
        username, password, times, roomid, seatid, daysofweek = user.values()
        if current_dayofweek not in daysofweek:
            continue
            
        s = logged_sessions[index]
        captcha_pool = captcha_pools[index]
        if s is None or captcha_pool is None:
            continue
            
        peak_hour_session_setup(s, roomid, seatid)
        active_sessions.append((s, captcha_pool, user, index))
    
    logging.info(f"🏁 高峰期抢座即将开始，活跃用户数: {len(active_sessions)}")
    
    # 并发提交所有用户的预约
    def submit_user_reservation(session_data):
        s, captcha_pool, user, index = session_data
        username, password, times, roomid, seatid, daysofweek = user.values()
        
        logging.info(f"🚀 用户 {username} 开始高峰期抢座")
        
        time_slots = times if isinstance(times[0], list) else [times]
        successful_bookings = []
        failed_bookings = []
        
        for slot_index, time_slot in enumerate(time_slots):
            logging.info(f"📅 用户 {username} 尝试预约时间段 {slot_index + 1}/{len(time_slots)}: {time_slot}")
            
            success = ultimate_speed_submit(
                s, time_slot, roomid, seatid[0], captcha_pool, action
            )
            
            if success:
                logging.info(f"🎉 用户 {username} 时间段 {time_slot} 抢座成功！")
                successful_bookings.append(time_slot)
                
                # 预约成功后短暂延迟，避免被系统限制，同时刷新验证码池
                if slot_index < len(time_slots) - 1:  # 不是最后一个时间段
                    delay_time = random.uniform(1.0, 2.0)
                    logging.info(f"⏱️ 用户 {username} 成功预约后延迟 {delay_time:.1f} 秒")
                    time.sleep(delay_time)
            else:
                logging.warning(f"💥 用户 {username} 时间段 {time_slot} 抢座失败")
                failed_bookings.append(time_slot)
                
                # 失败后也短暂延迟，避免频繁请求
                if slot_index < len(time_slots) - 1:
                    delay_time = random.uniform(0.5, 1.0)
                    logging.info(f"⏱️ 用户 {username} 失败后延迟 {delay_time:.1f} 秒")
                    time.sleep(delay_time)
        
        # 输出最终统计结果
        total_slots = len(time_slots)
        success_count = len(successful_bookings)
        failed_count = len(failed_bookings)
        
        logging.info(f"📊 用户 {username} 预约结果统计:")
        logging.info(f"   ✅ 成功预约: {success_count}/{total_slots} 个时间段")
        logging.info(f"   ❌ 失败预约: {failed_count}/{total_slots} 个时间段")
        
        if successful_bookings:
            logging.info(f"   🎯 成功时间段: {successful_bookings}")
        if failed_bookings:
            logging.info(f"   💔 失败时间段: {failed_bookings}")
        
        captcha_pool.stop()
    
    # 使用线程池并发执行
    with ThreadPoolExecutor(max_workers=len(active_sessions)) as executor:
        futures = [executor.submit(submit_user_reservation, session_data) 
                  for session_data in active_sessions]
        
        # 等待所有任务完成
        for future in futures:
            try:
                future.result(timeout=30)  # 增加超时时间以支持多时间段预约
            except Exception as e:
                logging.error(f"用户预约线程异常: {e}")


def main_peak_hour_optimized(users, action=False):
    logging.info(f"🌃 高峰期优化版启动 - 支持多时间段预约 (action={'on' if action else 'off'})")
    
    usernames, passwords = None, None
    if action:
        usernames, passwords = get_user_credentials(action)
    
    # 错峰登录
    logged_sessions, captcha_pools = staggered_login(users, usernames, passwords, action)
    
    # 等待验证码预加载时间
    microsecond_timing_wait(CAPTCHA_PRELOAD_AT, action)
    
    # 启动所有验证码池
    for pool in captcha_pools:
        if pool:
            pool.start_aggressive_preloading()
    
    logging.info("🔥 验证码池预热中，等待10秒...")
    time.sleep(10)
    
    # 精确等待到22:00:00
    microsecond_timing_wait(RESERVE_TARGET_TIME, action)
    
    # 启动高峰期抢座
    peak_hour_reservation(users, logged_sessions, captcha_pools, action)


if __name__ == "__main__":
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    parser = argparse.ArgumentParser(prog="Chaoxing Peak Hour Optimizer")
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
    func_dict = {"reserve": main_peak_hour_optimized}
    with open(args.user, "r+") as data:
        usersdata = json.load(data)["reserve"]
    func_dict[args.method](usersdata, args.action)
