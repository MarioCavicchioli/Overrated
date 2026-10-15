#!/bin/zsh
# Keeps the data service and its ngrok tunnel up for the day, always on the same URL
# (the team's .env files point at it). Checks every 30 s and restarts whichever is down.
# Run: nohup data/keepalive.sh >/dev/null 2>&1 &    Stop: pkill -f data/keepalive.sh
cd "${0:A:h}/.."
URL=https://undrilled-important-sputter.ngrok-free.dev
LOG=data/raw/_work
mkdir -p $LOG

while true; do
  if ! curl -sf -m 5 http://127.0.0.1:8000/health >/dev/null; then
    echo "$(date '+%F %T') data service down, restarting" >> $LOG/keepalive.log
    pkill -f "uvicorn data.service"
    nohup .venv/bin/uvicorn data.service:app --port 8000 >> $LOG/service.log 2>&1 &
    sleep 10
  fi
  if ! curl -sf -m 5 http://127.0.0.1:4040/api/tunnels | grep -q "$URL"; then
    echo "$(date '+%F %T') tunnel down or on the wrong URL, restarting" >> $LOG/keepalive.log
    pkill -f "ngrok http"
    nohup ngrok http 8000 --url=$URL --log=stdout >> $LOG/ngrok.log 2>&1 &
    sleep 10
  fi
  sleep 30
done
