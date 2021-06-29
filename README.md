# 选课脚本

```bash
pip3 install -r requirements.txt
pip3 install --upgrade libxduauth
cp config.json.sample config.json
修改config.json（记得去除所有注释）
python3 xk.py
```

参考`config.json.sample`填写`config.json`，然后执行`xk.py`

## 为什么要开源？

* 首先，在合理的频率限制下直接调用后端而不请求前端页面是能某种意义上减轻服务器压力的
* 其次，有同学利用抢课脚本进行不道德的课程售卖
* ⬆️这不是助长脚本风气么？
* ⬆️脚本的存在是不可避免的，写个脚本非常容易。开源能够进一步降低其门槛
* 为别人进一步完善作参考
