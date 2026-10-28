#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run sudo ./deploy/install.sh from the extracted project directory.' >&2
  exit 1
fi
SAUNA_USER=${SUDO_USER:-alpha}
SAUNA_GROUP=$(id -gn "$SAUNA_USER")
if [ -L /opt/sauna/current ]; then
  echo 'Already installed. Use sudo systemctl start sauna-updater.service for application updates.' >&2
  exit 1
fi
if systemctl is-active --quiet sauna-controller.service; then
  echo 'Existing controller is running; refusing to interrupt a possible session.' >&2
  exit 1
fi
test -s deploy/release-public.pem
test -f deploy/updater.py
export DEBIAN_FRONTEND=noninteractive
apt-get install -y ca-certificates openssl python3-venv python3-paho-mqtt python3-gpiozero python3-lgpio python3-spidev python3-smbus2 mosquitto-clients
install -d -m 0755 /opt/sauna /opt/sauna/releases /opt/sauna/updater /etc/sauna /var/lib/sauna-updater
SAUNA_VERSION=$(python3 -c 'import re; print(re.search(r"__version__ = \"([0-9.]+)\"", open("src/sauna_controller/__init__.py").read()).group(1))')
install -d -m 0755 "/opt/sauna/releases/$SAUNA_VERSION"
cp -R src "/opt/sauna/releases/$SAUNA_VERSION/"
ln -s "/opt/sauna/releases/$SAUNA_VERSION" /opt/sauna/current
install -m 0644 deploy/updater.py /opt/sauna/updater/updater.py
install -m 0644 deploy/release-public.pem /etc/sauna/release-public.pem
sed "s/user = \"alpha\"/user = \"$SAUNA_USER\"/" deploy/updater.toml > /etc/sauna/updater.toml
python3 -m venv --system-site-packages /opt/sauna/venv
VENV_SITE=$(/opt/sauna/venv/bin/python -c 'import site; print(site.getsitepackages()[0])')
printf '%s\n' /opt/sauna/current/src > "$VENV_SITE/sauna-controller.pth"
if [ ! -e /etc/sauna/controller.toml ]; then
  install -o "$SAUNA_USER" -g "$SAUNA_GROUP" -m 0640 config/controller.toml /etc/sauna/controller.toml
fi
if [ ! -e /etc/sauna/mqtt.env ]; then
  install -o "$SAUNA_USER" -g "$SAUNA_GROUP" -m 0600 /dev/null /etc/sauna/mqtt.env
fi
if [ -f .env ] && [ ! -e /etc/sauna/runtime.env ]; then
  PYTHONPATH=src python3 -c 'from sauna_controller.environment import write_runtime_env; write_runtime_env(".env", "/etc/sauna/runtime.env")'
  chown "$SAUNA_USER:$SAUNA_GROUP" /etc/sauna/runtime.env
fi
sed -e "s/@USER@/$SAUNA_USER/g" -e "s/@GROUP@/$SAUNA_GROUP/g" deploy/sauna-controller.service > /etc/systemd/system/sauna-controller.service
install -m 0644 deploy/sauna-updater.service deploy/sauna-updater.timer /etc/systemd/system/
cat > /usr/local/bin/saunactl <<'EOF'
#!/bin/sh
exec /opt/sauna/venv/bin/python -m sauna_controller.cli "$@"
EOF
chmod 0755 /usr/local/bin/saunactl
/opt/sauna/venv/bin/python -m sauna_controller.app --check --env-file /dev/null
systemctl daemon-reload
systemctl enable --now sauna-controller.service
systemctl enable --now sauna-updater.timer
systemctl --no-pager status sauna-controller.service
systemctl --no-pager list-timers sauna-updater.timer
