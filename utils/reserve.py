from utils import AES_Encrypt, enc, generate_captcha_key, verify_param
import json
import requests
import re
import time
import logging
import datetime
from urllib3.exceptions import InsecureRequestWarning

def get_date(day_offset: int = 0):
    today = datetime.datetime.now().date()
    offset_day = today + datetime.timedelta(days=day_offset)
    tomorrow = offset_day.strftime("%Y-%m-%d")
    return tomorrow

class reserve:
    def __init__(
        self,
        sleep_time=0.2,
        max_attempt=50,
        enable_slider=False,
        reserve_next_day=False,
    ):
        self.login_page = (
            "https://passport2.chaoxing.com/mlogin?loginType=1&newversion=true&fid="
        )
        self.url = (
            "https://office.chaoxing.com/front/third/apps/seat/code?id={}&seatNum={}"
        )
        self.submit_url = "https://office.chaoxing.com/data/apps/seat/submit"
        self.seat_url = "https://office.chaoxing.com/data/apps/seat/getusedtimes"
        self.login_url = "https://passport2.chaoxing.com/fanyalogin"
        self.token = ""
        self.success_times = 0
        self.fail_dict = []
        self.submit_msg = []
        self.requests = requests.session()
        self.token_pattern = re.compile("token = '(.*?)'")
        self.headers = {
            "Referer": "https://office.chaoxing.com/",
            "Host": "captcha.chaoxing.com",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        }
        self.login_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "accept-encoding": "gzip, deflate, br, zstd",
            "cache-control": "no-cache",
            "Connection": "keep-alive",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 10_3_1 like Mac OS X) AppleWebKit/603.1.3 (KHTML, like Gecko) Version/10.0 Mobile/14E304 Safari/602.1 wechatdevtools/1.05.2109131 MicroMessenger/8.0.5 Language/zh_CN webview/16364215743155638",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Host": "passport2.chaoxing.com",
        }

        self.sleep_time = sleep_time
        self.max_attempt = max_attempt
        self.enable_slider = enable_slider
        self.reserve_next_day = reserve_next_day
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    def refresh_session(self):
        """刷新session状态，防止过期"""
        try:
            # 访问主页面保持session活跃
            self.requests.get("https://office.chaoxing.com/", verify=False)
            # 访问座位相关页面
            self.requests.get("https://office.chaoxing.com/data/apps/seat/getusedtimes", verify=False)
            logging.debug("🔄 Session已刷新")
        except Exception as e:
            logging.warning(f"⚠️ Session刷新失败: {e}")

    # login and page token
    def _get_page_token(self, url, require_value=False):
        try:
            # 在获取token前刷新session
            self.refresh_session()
            
            response = self.requests.get(url=url, verify=False)
            html = response.content.decode("utf-8")
            
            # matches = re.findall(r"token = \'(.*?)\'", html)
            matches = re.findall(r'id="submit_enc"\s+value="(.*?)"', html)
            if not matches:
                matches = re.findall(r"token\s*=\s*'([^']*)'", html)
            if not matches:
                matches = re.findall(r'name="token"\s+value="([^"]*)"', html)
            
            value_matches = None
            if require_value:
                value_matches = re.findall(r'value="(.*?)"', html)
                if not matches:
                    logging.error(f"Failed to get token from {url}")
                    return "", ""
                if not value_matches:
                    logging.error(f"Failed to get submit value from {url}")
                    return matches[0], ""
            
            return matches[0] if matches else "", value_matches[0] if value_matches else ""
        except Exception as e:
            logging.error(f"❌ 获取页面token失败: {e}")
            return "", ""

    def get_login_status(self):
        self.requests.headers = self.login_headers
        self.requests.get(url=self.login_page, verify=False)

    def login(self, username, password):
        username_enc = AES_Encrypt(username)
        password_enc = AES_Encrypt(password)
        parm = {
            "fid": -1,
            "uname": username_enc,
            "password": password_enc,
            "refer": "http%3A%2F%2Foffice.chaoxing.com%2Ffront%2Fthird%2Fapps%2Fseat%2Fcode%3Fid%3D4219%26seatNum%3D380",
            "t": True,
        }
        jsons = self.requests.post(url=self.login_url, params=parm, verify=False)
        obj = jsons.json()
        if obj["status"]:
            logging.info(f"User {username_enc} login successfully")
            return (True, "")
        else:
            logging.info(
                f"User {username} login failed. Please check you password and username! "
            )
            return (False, obj["msg2"])

    # extra: get roomid
    def roomid(self, encode):
        url = f"https://office.chaoxing.com/data/apps/seat/room/list?cpage=1&pageSize=100&firstLevelName=&secondLevelName=&thirdLevelName=&deptIdEnc={encode}"
        json_data = self.requests.get(url=url).content.decode("utf-8")
        ori_data = json.loads(json_data)
        for i in ori_data["data"]["seatRoomList"]:
            info = f'{i["firstLevelName"]}-{i["secondLevelName"]}-{i["thirdLevelName"]} id为：{i["id"]}'
            print(info)

    # solve captcha - 增强版本，防止过期
    def resolve_captcha(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logging.info(f"Start to resolve captcha token (attempt {attempt + 1}/{max_retries})")
                
                # 在获取验证码前刷新session
                self.refresh_session()
                
                captcha_token, bg, tp = self.get_slide_captcha_data()
                logging.info(f"Successfully get prepared captcha_token {captcha_token}")
                logging.info(f"Captcha Image URL-small {tp}, URL-big {bg}")
                
                x = self.x_distance(bg, tp)
                logging.info(f"Successfully calculate the captcha distance {x}")

                params = {
                    "callback": "jQuery33109180509737430778_1716381333117",
                    "captchaId": "42sxgHoTPTKbt0uZxPJ7ssOvtXr3ZgZ1",
                    "type": "slide",
                    "token": captcha_token,
                    "textClickArr": json.dumps([{"x": x}]),
                    "coordinate": json.dumps([]),
                    "runEnv": "10",
                    "version": "1.1.18",
                    "_": int(time.time() * 1000),
                }
                response = self.requests.get(
                    f"https://captcha.chaoxing.com/captcha/check/verification/result",
                    params=params,
                    headers=self.headers,
                )
                text = response.text.replace(
                    "jQuery33109180509737430778_1716381333117(", ""
