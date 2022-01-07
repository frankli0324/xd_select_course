import json
import time
import threading
import inspect
import ctypes

from libxduauth import XKSession
from reprint import output
from requests.exceptions import RequestException

with open('config.json', 'r') as f:
    config = json.load(f)
    auth = config['authentication']

BASE_URL = 'http://xk.xidian.edu.cn/xsxk'
TYPES = {
    'public': 'XGKC',  # 校公选课
    'program': 'FANKC',  # 方案内课程
    'gym': 'TYKC',  # 体育课程
    'recommended': 'TJKC',  # 推荐课程
}
info = {}
status = {}
course_list = set()
target_course_available = {}


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

    def select_class(self, type_, class_):
        select_resp = ses.post(BASE_URL + '/elective/clazz/add', data={
            'clazzType': type_,
            'clazzId': class_['JXBID'],
            'secretVal': class_['secretVal'],
        }).json()
        return select_resp['code'] == 200, select_resp['msg']

    def delete_class(self, type_, class_):
        delete_resp = ses.post(BASE_URL + '/elective/clazz/add', data={
            'clazzType': type_,
            'clazzId': class_['JXBID'],
            'secretVal': class_['secretVal'],
        }).json()
        return delete_resp['code'] == 200, delete_resp['msg']

    def run(self):
        result = False
        while not result and self.ensure_available():
            pick = None
            if self.class_id != 'any':
                for available in target_course_available[self.course_id]:
                    if available['JXBID'] == self.class_id:
                        pick = available
                        break
                else:
                    print('没有找到班级号符合的班级，将尝试选择第一个可用班级')
            if not pick:
                pick = target_course_available[self.course_id][0]
            self.status = f'正在尝试 {pick["KCH"]}'
            result, msg = self.select_class(self.course_type, pick)
            if not result:
                self.status = f'班级：{pick["KCH"]} 尝试失败,原因为："{msg}"'
                time.sleep(0.5)
            else:
                self.status = '选课成功'
                break

    def __str__(self):
        return self.status


class GetClasses(KThread):
    def __init__(self, types):
        super().__init__()
        self.types = types
        self.status = ''

    def get_classes(self, _type):
        def list_courses(page=1):
            try:
                return ses.post(f'{BASE_URL}/elective/clazz/list', json={
                    "teachingClassType": _type, "campus": ses.user['campus'],
                    "pageNumber": page, "pageSize": 500, "orderBy": "",
                }).json()
            except RequestException as e:
                return {'code': 500, 'msg': str(type(e))}
        query_resp = list_courses()
        if query_resp['code'] != 200:
            self.status = f'获取可选课程失败，原因为: "{query_resp["msg"]}"，正在重试'
        while query_resp['code'] != 200:
            self.status += '.'
            query_resp = list_courses()
        total = query_resp['data']['total']
        courses = query_resp['data']['rows']
        for i in range(2, (total + 1) // 500):
            courses += list_courses(i)['data']['rows']
        for course in courses:
            if course['KCH'] in course_list:
                if 'tcList' in course:
                    target_course_available[course['KCH']] = [
                        class_ for class_ in course['tcList']
                        if class_['SFYM'] == '0' and class_['SFCT'] == '0'
                    ]
                    if len(target_course_available[course['KCH']]) == 0:
                        target_course_available.pop(course['KCH'])
                else:
                    if course['SFYM'] == '0' and course['SFCT'] == '0':
                        target_course_available[course['KCH']] = [course]
        self.status = (
            f'获取到{total}条课程数据，'
            f'{len(target_course_available)}门课程中的'
            f'{sum((len(i) for i in target_course_available))}个班级可选'
        )

    def run(self):
        while True:
            for _type in self.types:
                self.get_classes(_type)

    def __str__(self):
        return self.status


if __name__ == "__main__":
    ses = XKSession(auth['username'], auth['password'])
    ses.request = rate_limited(ses.request)
    print('当前轮次：' + ses.current_batch['name'])

    if input('是否开始抢课?(y/n)') == 'n':
        print('行吧')
        exit(0)
    with output(output_type="dict") as progress:
        progress['[available]'] = GetClasses(
            {TYPES[i] for i in config["open_types"]}
        )
        progress['[available]'].start()
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
