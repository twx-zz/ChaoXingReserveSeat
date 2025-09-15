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
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import random
from urllib3.util.retry import Retry

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

# ================= 超速模式参数 - 专治303超时 =================
SLEEPTIME = 0.01  # 极短间隔
RESERVE_TARGET_TIME = "15:23:00"  
ENABLE_SLIDER = True  
MAX_ATTEMPT = 1  # 单次尝试，极速失败
RESERVE_NEXT_DAY = False
CAPTCHA_POOL_SIZE = 5  # 更大的验证码池
CAPTCHA_PRELOAD_AT = "15:22:55"  # 提前15秒预加载

# 🚀 超时控制 - 严格限制
PARALLEL_TIMEOUT = 2  # 并行获取超时2秒
SUBMIT_TIMEOUT = 3    # 提交超时3秒  
GLOBAL_TIMEOUT = 5    # 全局session超时5秒
SLOT_INTERVAL = 0.05  # 时间段间隔50毫秒

# 🔗 连接池优化
POOL_CONNECTIONS = 10
POOL_MAXSIZE = 20
# ==========================================

class UltraFastCaptchaPool:
    """超快速验证码池 - 专治303超时"""
    def __init__(self, session, pool_size=CAPTCHA_POOL_SIZE):
        self.session = session
        self.pool_size = pool_size
        self.captcha_queue = Queue()
        self.is_active = True
        self.lock = threading.Lock()
        self.generation_count = 0
        
    def start_ultra_fast_preloading(self):
        """超快速验证码预加载"""
        logging.info(f"🚀 启动超速验证码池，目标数量: {self.pool_size}")
        
        def ultra_worker():
            while self.is_active:
                try:
                    current_size = self.captcha_queue.qsize()
                    
                    if current_size < self.pool_size:
                        # 设置验证码获取超时
                        start_time = time.time()
                        try:
                            captcha = self.session.resolve_captcha()
                            resolve_time = time.time() - start_time
                            
                            if captcha and resolve_time < 3:  # 3秒内获取的验证码才有效
                                self.captcha_queue.put({
                                    'captcha': captcha,
                                    'timestamp': time.time(),
                                    'id': self.generation_count,
                                    'resolve_time': resolve_time
                                })
                                self.generation_count += 1
                                logging.info(f"⚡ 验证码#{self.generation_count} 超速加载成功 ({resolve_time:.2f}s)，池大小: {current_size + 1}")
                            else:
                                logging.warning(f"⚠️ 验证码获取太慢 ({resolve_time:.2f}s)，丢弃")
                                
                        except Exception as e:
                            logging.warning(f"⚠️ 验证码获取异常: {e}")
                    
                    # 超快速清理
                    self._ultra_fast_cleanup()
                    
                    # 极短休息时间
                    time.sleep(0.05)
                    
                except Exception as e:
                    logging.warning(f"⚠️ 验证码worker异常: {e}")
                    time.sleep(0.1)
        
        # 启动3个并发生成线程 - 更多并发
        for i in range(3):
            thread = threading.Thread(target=ultra_worker, daemon=True)
            thread.start()
    
    def _ultra_fast_cleanup(self):
        """超快速清理过期验证码"""
        current_time = time.time()
        temp_queue = Queue()
        expired_count = 0
        
        while not self.captcha_queue.empty():
            try:
                captcha_data = self.captcha_queue.get_nowait()
                # 验证码有效期缩短至8秒
                if current_time - captcha_data['timestamp'] < 8:
                    temp_queue.put(captcha_data)
                else:
                    expired_count += 1
            except:
                break
        
        # 快速放回
        while not temp_queue.empty():
            try:
                self.captcha_queue.put_nowait(temp_queue.get_nowait())
            except:
                break
            
        if expired_count > 0:
            logging.info(f"🧹 超速清理 {expired_count} 个过期验证码")
    
    def get_lightning_captcha(self):
        """闪电获取验证码 - 专治303超时"""
        best_captcha = None
        newest_time = 0
        
        temp_captchas = []
        # 快速遍历找最新的
        for _ in range(min(5, self.captcha_queue.qsize())):  # 最多检查5个
            try:
                captcha_data = self.captcha_queue.get_nowait()
                temp_captchas.append(captcha_data)
                
                if captcha_data['timestamp'] > newest_time:
                    newest_time = captcha_data['timestamp']
                    best_captcha = captcha_data
            except:
                break
        
        # 快速放回其他验证码
        for captcha_data in temp_captchas:
            if captcha_data != best_captcha:
                try:
                    self.captcha_queue.put_nowait(captcha_data)
                except:
                    pass
        
        if best_captcha and time.time() - best_captcha['timestamp'] < 6:
            age = time.time() - best_captcha['timestamp']
            logging.info(f"⚡ 闪电使用验证码#{best_captcha['id']} (年龄:{age:.1f}s, 生成用时:{best_captcha['resolve_time']:.2f}s)")
            return best_captcha['captcha']
        else:
            # 紧急超时生成
            logging.info("🚨 紧急生成验证码 (3秒超时)")
            start_time = time.time()
            try:
                captcha = self.session.resolve_captcha()
                gen_time = time.time() - start_time
                if gen_time > 2:
                    logging.warning(f"⚠️ 紧急验证码耗时过长: {gen_time:.2f}s")
                return captcha
            except Exception as e:
                logging.error(f"💥 紧急验证码生成失败: {e}")
                return None
    
    def stop(self):
        self.is_active = False


def ultra_fast_session_setup(session, roomid, seatid):
    """超快速session设置 - 专治303超时"""
    try:
        logging.info("🚀 超速Session设置中...")
        
        # 创建高性能适配器
        retry_strategy = Retry(
            total=0,  # 不重试
            backoff_factor=0,
            status_forcelist=[]
        )
        
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=POOL_CONNECTIONS,
            pool_maxsize=POOL_MAXSIZE,
            max_retries=retry_strategy
        )
        
        # 挂载适配器
        session.requests.mount("http://", adapter)
        session.requests.mount("https://", adapter)
        
        # 优化headers
        session.requests.headers.update({
            "Host": "office.chaoxing.com",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
            "Pragma": "no-cache",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json,text/html,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
        
        # 严格超时设置
        session.requests.timeout = GLOBAL_TIMEOUT
        
        logging.info("✅ 超速Session设置完成")
    except Exception as e:
        logging.warning(f"⚠️ Session设置失败: {e}")


def lightning_submit(session, times, roomid, seatid, captcha_pool, action):
    """闪电提交 - 专治303超时"""
    submit_start = time.time()
    
    try:
        logging.info(f"⚡ 闪电提交开始: {times}")
        
        # 🚀 并行获取验证码和token - 严格2秒超时
        def get_captcha_with_timeout():
            return captcha_pool.get_lightning_captcha()
        
        def get_token_with_timeout():
            return session._get_page_token(
                session.url.format(roomid, seatid), require_value=True
            )
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            captcha_future = executor.submit(get_captcha_with_timeout)
            token_future = executor.submit(get_token_with_timeout)
            
            try:
                # 严格2秒超时
                captcha = captcha_future.result(timeout=PARALLEL_TIMEOUT)
                token, value = token_future.result(timeout=PARALLEL_TIMEOUT)
            except TimeoutError:
                logging.error("💥 并行获取超时！")
                return False
        
        prep_time = time.time() - submit_start
        
        if not captcha:
            logging.error("💥 验证码获取失败！")
            return False
            
        if not token:
            logging.error("💥 Token获取失败！")
            return False
        
        logging.info(f"⚡ 并行准备完成 Token:{token[:10]}... 耗时:{prep_time:.3f}s")
        
        # 🚀 立即提交 - 严格3秒超时
        def submit_with_timeout():
            return session.get_submit(
                session.submit_url,
                times=times,
                token=token,
                roomid=roomid,
                seatid=seatid,
                captcha=captcha,
                action=action,
                value=value,
            )
        
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(submit_with_timeout)
            try:
                success = future.result(timeout=SUBMIT_TIMEOUT)
            except TimeoutError:
                logging.error("💥 提交请求超时！")
                return False
        
        total_time = time.time() - submit_start
        
        if success:
            logging.info(f"🚀 闪电提交成功！总耗时:{total_time:.3f}s (准备:{prep_time:.3f}s)")
        else:
            logging.warning(f"❌ 提交失败，总耗时:{total_time:.3f}s")
            
        return success
        
    except Exception as e:
        total_time = time.time() - submit_start
        logging.error(f"💥 闪电提交异常: {e}，总耗时:{total_time:.3f}s")
        return False


def lightning_login(users, usernames, passwords, action):
    """闪电登录 - 快速批量登录"""
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
        
        # 极短错峰延迟
        if index > 0:
            stagger_delay = random.uniform(0.1, 0.3)
            time.sleep(stagger_delay)
            
        logging.info(f"User {username}: 闪电登录中...")
        
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
            captcha_pools.append(UltraFastCaptchaPool(s, CAPTCHA_POOL_SIZE))
            logging.info(f"✅ {username} 闪电登录成功")
        else:
            logging.error(f"❌ {username} 登录失败: {msg}")
            logged_sessions.append(None)
            captcha_pools.append(None)
    
    return logged_sessions, captcha_pools


def precision_timing_wait(target_time, action):
    """精确时间等待 - 毫秒级"""
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
        
        if wait_seconds > 2:
            time.sleep(0.5)
        elif wait_seconds > 0.2:
            time.sleep(0.05)
        elif wait_seconds > 0.02:
            time.sleep(0.005)
        else:
            break
    
    total_wait = time.time() - start_wait
    logging.info(f"⚡ 精确等待完成，等待了 {total_wait:.3f} 秒")


def ultra_fast_reservation(users, logged_sessions, captcha_pools, action):
    """超快速预约 - 专治303超时"""
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
            
        ultra_fast_session_setup(s, roomid, seatid)
        active_sessions.append((s, captcha_pool, user, index))
    
    logging.info(f"⚡ 超快速预约开始，活跃用户数: {len(active_sessions)}")
    
    def ultra_fast_user_reservation(session_data):
        s, captcha_pool, user, index = session_data
        username, password, times, roomid, seatid, daysofweek = user.values()
        
        logging.info(f"🚀 用户 {username} 开始超速预约")
        
        time_slots = times if isinstance(times[0], list) else [times]
        successful_bookings = []
        failed_bookings = []
        
        user_start_time = time.time()
        
        for slot_index, time_slot in enumerate(time_slots):
            slot_start_time = time.time()
            logging.info(f"⚡ 用户 {username} 闪电预约 {slot_index + 1}/{len(time_slots)}: {time_slot}")
            
            success = lightning_submit(
                s, time_slot, roomid, seatid[0], captcha_pool, action
            )
            
            slot_time = time.time() - slot_start_time
            
            if success:
                logging.info(f"🎉 用户 {username} 时间段 {time_slot} 闪电成功！({slot_time:.3f}s)")
                successful_bookings.append(time_slot)
            else:
                logging.warning(f"💥 用户 {username} 时间段 {time_slot} 失败 ({slot_time:.3f}s)")
                failed_bookings.append(time_slot)
            
            # 极短间隔 - 50毫秒
            if slot_index < len(time_slots) - 1:
                time.sleep(SLOT_INTERVAL)
        
        # 用户总耗时统计
        user_total_time = time.time() - user_start_time
        success_count = len(successful_bookings)
        failed_count = len(failed_bookings)
        
        logging.info(f"📊 用户 {username} 超速完成 (总耗时:{user_total_time:.3f}s):")
        logging.info(f"   ✅ 成功: {success_count}/{len(time_slots)} 个")
        logging.info(f"   ❌ 失败: {failed_count}/{len(time_slots)} 个")
        
        if successful_bookings:
            logging.info(f"   🎯 成功时间段: {successful_bookings}")
        if failed_bookings:
            logging.info(f"   💔 失败时间段: {failed_bookings}")
        
        captcha_pool.stop()
        
        return {
            'username': username,
            'success_count': success_count,
            'total_time': user_total_time,
            'successful_bookings': successful_bookings
        }
    
    # 并发执行所有用户
    total_start_time = time.time()
    results = []
    
    with ThreadPoolExecutor(max_workers=len(active_sessions)) as executor:
        futures = [executor.submit(ultra_fast_user_reservation, session_data) 
                  for session_data in active_sessions]
        
        for future in futures:
            try:
                result = future.result(timeout=15)  # 每个用户最多15秒
                results.append(result)
            except Exception as e:
                logging.error(f"用户预约线程异常: {e}")
    
    # 最终统计
    total_time = time.time() - total_start_time
    total_success = sum(r['success_count'] for r in results)
    logging.info(f"🎯 全部用户超速完成！总耗时:{total_time:.3f}s，总成功:{total_success}个时间段")


def main_ultra_fast_optimized(users, action=False):
    logging.info(f"⚡ 超速版启动 - 专治303超时 (action={'on' if action else 'off'})")
    
    usernames, passwords = None, None
    if action:
        usernames, passwords = get_user_credentials(action)
    
    # 闪电登录
    logged_sessions, captcha_pools = lightning_login(users, usernames, passwords, action)
    
    # 等待验证码预加载时间
    precision_timing_wait(CAPTCHA_PRELOAD_AT, action)
    
    # 启动所有验证码池
    for pool in captcha_pools:
        if pool:
            pool.start_ultra_fast_preloading()
    
    logging.info("🚀 验证码池超速预热中，等待15秒...")
    time.sleep(15)
    
    # 精确等待到22:00:00
    precision_timing_wait(RESERVE_TARGET_TIME, action)
    
    # 启动超快速抢座
    ultra_fast_reservation(users, logged_sessions, captcha_pools, action)


if __name__ == "__main__":
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    parser = argparse.ArgumentParser(prog="Chaoxing Ultra Fast Optimizer - Anti 303")
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
    func_dict = {"reserve": main_ultra_fast_optimized}
    with open(args.user, "r+") as data:
        usersdata = json.load(data)["reserve"]
    func_dict[args.method](usersdata, args.action)
