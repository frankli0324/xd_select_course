import json
import time
import threading
import inspect
import ctypes

from libxduauth import XKSession
from reprint import output

with open('config.json', 'r') as f:
    config = json.load(f)
    auth = config['authentication']

BASE_URL = 'http://xk.xidian.edu.cn/xsxkapp/sys/xsxkapp'
TYPES = {
    'public': 'XGXK',  # 校公选课
    'program': 'FANKC',  # 方案内课程
    'gym': 'TYKC',  # 体育课程
    'recommended': 'TJKC'
}
info = {}
status = {}
course_list = set()
target_course_available = {}


def get_info():
    resp = ses.get(
        BASE_URL + '/student/' + auth['username'] + '.do',
        params={'timestamp': int(time.time() * 1000)}
    ).json()
    info['campus'] = resp['data']['campus']
    for batch in resp['data']['electiveBatchList']:
        if batch['canSelect'] == '1':
            print('可选轮次:' + batch['name'])
            info['elective_batch_code'] = batch['code']
            break
    if 'elective_batch_code' not in info:
        raise Exception('暂无可选轮次')


def rate_limited(func):
    def _(*args, **kwargs):
        result = func(*args, **kwargs)
        time.sleep(0.1)  # 全局限制，短一点应该没问题
        return result
    return _


def _async_raise(tid, exctype):
    """Raises the exception, causing the thread to exit"""
    if not inspect.isclass(exctype):
        raise TypeError("Only types can be raised (not instances)")
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(tid), ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("Invalid thread ID")
    elif res != 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, 0)
        raise SystemError("PyThreadState_SetAsyncExc failed")


# https://github.com/203Null/kthread
class KThread(threading.Thread):
    def _get_my_tid(self):
        if not self.is_alive():
            raise threading.ThreadError("Thread is not active")
        if hasattr(self, "_thread_id"):
            return self._thread_id
        for tid, tobj in threading._active.items():
            if tobj is self:
                self._thread_id = tid
                return tid
        raise AssertionError("Could not determine the thread's ID")

    def raise_exc(self, exctype):
        _async_raise(self._get_my_tid(), exctype)

    def terminate(self):
        self.raise_exc(SystemExit)

    def kill(self):
        self.terminate()

    def exit(self):
        self.terminate()


class Job(KThread):
    def __init__(self, course_type, course_id, class_id='any'):
        super().__init__()
        self.course_type = course_type
        self.course_id = course_id
        self.class_id = class_id
        self.status = ''

    def ensure_course_available(self):
        if self.course_id not in target_course_available:
            rnd = 0
            while self.course_id not in target_course_available:
                self.status = '暂无可选班级，等待中' + '.' * (rnd % 3 + 1)
                rnd += 1
                time.sleep(1)
            self.status = '发现了可选班级'
        return True

    def ensure_class_available(self):
        if self.class_id not in target_course_available[self.course_id]:
            self.status = '班级已满或冲突'
            return False
        return True

    def ensure_available(self):
        if not self.ensure_course_available():
            return False
        if self.class_id != 'any' and self.ensure_class_available():
            return False
        return True

    def select_class(self, type_, class_id):
        req_data = json.dumps({"data": {
            "operationType": "1",
            "studentCode": auth['username'],
            "electiveBatchCode": info['elective_batch_code'],
            "teachingClassId": class_id,
            "isMajor": "1",
            "campus": info['campus'],
            "teachingClassType": type_
        }})
        select_resp = ses.post(
            BASE_URL + '/elective/volunteer.do',
            data={'addParam': req_data}
        ).json()
        return select_resp['code'] == '1', select_resp['msg']

    def delete_class(self, class_id):
        req_data = json.dumps({"data": {
            "operationType": "2",
            "studentCode": auth['username'],
            "electiveBatchCode": info['elective_batch_code'],
            "teachingClassId": class_id,
            "isMajor": "1"
        }})
        delete_resp = ses.post(
            BASE_URL + '/elective/deleteVolunteer.do',
            data={'deleteParam': req_data}
        ).json()
        return delete_resp['code'] == '1', delete_resp['msg']

    def run(self):
        result = False
        while not result and self.ensure_available():
            class_id = target_course_available[self.course_id][0] \
                if self.class_id == 'any' else self.class_id
            self.status = f'正在尝试 {class_id}'
            result, msg = self.select_class(self.course_type, class_id)
            if not result:
                self.status = f'班级：{class_id} 尝试失败,原因为："{msg}"'
            else:
                self.status = '选课成功'
                break

    def __str__(self):
        return self.status


class GetClasses(KThread):
    def __init__(self, types):
        super().__init__()
        self.types = types

    @staticmethod
    def get_classes(_type):
        query_setting = json.dumps({"data": {
            "studentCode": auth['username'],
            "campus": info['campus'],
            "electiveBatchCode": info['elective_batch_code'],
            "isMajor": "1",
            "teachingClassType": TYPES[_type],
            "checkConflict": "2",
            "checkCapacity": "2",
            "queryContent": ""
        }, "pageSize": "500", "pageNumber": "0", "order": "null"})
        query_resp = ses.post(
            f'{BASE_URL}/elective/{_type}Course.do',
            data={'querySetting': query_setting}
        ).json()
        if query_resp['code'] != '1':
            print(f'获取可选课程失败，原因为: "{query_resp["msg"]}"，正在重试')
        while query_resp['code'] != '1':
            print('.', end='', flush=True)
            query_resp = ses.post(
                f'{BASE_URL}/elective/{_type}Course.do',
                data={'querySetting': query_setting}
            ).json()
        for course in query_resp['dataList']:
            if course['courseNumber'] in course_list:
                if 'tcList' in course:
                    target_course_available[course['courseNumber']] = [
                        class_['teachingClassID'] for class_ in course['tcList']
                        if class_['isFull'] == '0' and class_['isConflict'] == '0'
                    ]
                else:
                    target_course_available[course['courseNumber']] = [
                        course['teachingClassID']
                    ] if course['isFull'] == '0' and course['isConflict'] == '0' \
                        else []
                if len(target_course_available[course['courseNumber']]) == 0:
                    target_course_available.pop(course['courseNumber'])

    def run(self):
        while True:
            for _type in self.types:
                self.get_classes(_type)


if __name__ == '__main__':
    ses = XKSession(auth['username'], auth['password'])
    ses.request = rate_limited(ses.request)
    print('token为' + ses.token)
    get_info()

    if input('是否开始抢课?(y/n)') == 'n':
        print('行吧')
        exit(0)
    get_class_thread = GetClasses(config["open_types"])
    get_class_thread.start()
    with output(output_type="dict") as progress:
        for k, v in config['courses'].items():
            if k not in TYPES:
                continue
            for course_id, classes in v.items():
                course_list.add(course_id)
                if classes:
                    for c in classes:
                        name = f'{course_id}[{c}]'
                        progress[name] = Job(TYPES[k], course_id, c)
                        progress[name].start()
                else:
                    progress[course_id] = Job(TYPES[k], course_id)
                    progress[course_id].start()
        try:
            while True:
                for k in progress:
                    progress[k] = progress[k]  # 刷新输出
                time.sleep(0.1)
        except KeyboardInterrupt:
            for i in progress.values():
                if i.is_alive():
                    i.terminate()
            get_class_thread.terminate()
