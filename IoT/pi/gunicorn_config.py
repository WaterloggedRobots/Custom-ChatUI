# Gunicorn settings for esp_api_server.py on the Orange Pi.
# Used by deploy/esp-api.service:  gunicorn --config gunicorn_config.py esp_api_server:app
import os

# Only local: Nginx proxies port 80 to this.
bind = os.environ.get("API_BIND", "127.0.0.1:8000")
backlog = 2048

# Exactly one worker. The MQTT connection and the requests waiting for a device reply
# (?wait=N) live inside the process, so a reply that arrives in worker A can't reach a
# request waiting in worker B. Threads let several requests wait at the same time.
workers = 1
worker_class = "gthread"
threads = 8
# Must stay above the API's longest wait (15 s).
timeout = 30
graceful_timeout = 10
keepalive = 5
# Don't preload: the MQTT client starts a thread when the app is imported, and threads
# don't survive the fork from the master into the worker.
preload_app = False

# Logging (the directory must exist and belong to the service user)
log_dir = os.environ.get("ESP_API_LOG_DIR", "/var/log/esp-api")
accesslog = f"{log_dir}/gunicorn-access.log"
errorlog = f"{log_dir}/gunicorn-error.log"
loglevel = "info"

proc_name = "esp-api"
# systemd runs it in the foreground as the right user, so no daemon/user/group/pidfile here.
