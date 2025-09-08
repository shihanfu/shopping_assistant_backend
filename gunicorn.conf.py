# /home/ubuntu/shopping_assistant/shopping_assistant_backend/gunicorn.conf.py

bind = "0.0.0.0:5000"     # 监听所有地址的5000端口
workers = 2               # worker进程数，小机子2个就够
threads = 4               # 每个worker线程数
worker_class = "gthread"  # 适合Flask阻塞型IO
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"           # 输出到stdout
errorlog = "-"
loglevel = "info"
capture_output = True


