# Sauna Controller

Lokale Python-Steuerung für Raspberry Pi Zero W (ARMv6), mit Home-Assistant-Anbindung per MQTT Discovery.

**Aktueller Stand:** Implementierung und lokale Softwaretests vorhanden. Eine Installation auf dem echten Pi und die Anmeldung am Home-Assistant-MQTT-Broker sind noch nicht bestätigt. Es gibt noch keine Prüfung mit echten Sensoren, Schützen oder Heizlast. Die Konfiguration startet im Überwachungsmodus und initialisiert keine GPIO-Ausgänge.

Enthalten sind Zweipunktregelung am PT100, Hysterese/Mindestpause, Schützrückmeldungen, monotone Sitzungs- und Aufheizfristen, lokale Temperaturprofile, persistente Fehlerverriegelung, MAX31865-/SHT41-Treiber, ein separater Sensorprozess, ein systemd-Dienst und MQTT Discovery für Klima, Sensoren, Diagnose, Programme und Licht.

## Betriebsarten

- `monitor`: echte Sensoren lesen, Zustände per MQTT melden; keine GPIO-Ausgänge, kein Heizstart. Rückmeldungen ohne angeschlossene GPIO-Erfassung erscheinen als nicht verfügbar.
- `simulation`: simulierte Temperaturen und Schütze; keine Hardwarezugriffe. Eigene Gerätekennung mit `_sim`, damit keine simulierten Werte mit echten Messungen vermischt werden.
- `hardware`: reale GPIOs und Sensoren. Erfordert `heating_enabled = true` und `safety_verified = true`. Diese Schalter sind eine Inbetriebnahmesperre, kein technischer Nachweis der Sicherheit. Vor ihrer Änderung müssen Verdrahtung, Grenzwerte und unabhängige Abschaltung tatsächlich geprüft sein.

Ein PT100-Sollwert bezieht sich auf den Fühler über dem Ofen. Die Banktemperatur ist eine separate Messung. Alle vorkonfigurierten Temperaturprofile und Grenzwerte sind Beispiele und nicht für einen unbekannten Ofen freigegeben.

## Lokale Entwicklung und Test

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m sauna_controller.app --config config/simulation.toml --state-dir /tmp/sauna-state --socket /tmp/sauna.sock
```

In einem zweiten Terminal:

```sh
PYTHONPATH=src python3 -m sauna_controller.cli --socket /tmp/sauna.sock status
PYTHONPATH=src python3 -m sauna_controller.cli --socket /tmp/sauna.sock arm
PYTHONPATH=src python3 -m sauna_controller.cli --socket /tmp/sauna.sock mode heat
PYTHONPATH=src python3 -m sauna_controller.cli --socket /tmp/sauna.sock stop
```

CLI-Freigabe und CLI-Fehlerreset sind nur in der Simulation erlaubt. Im Hardwarebetrieb: Taster an GPIO5 kurz drücken und loslassen, um für 60 s eine Sitzung freizugeben. Bei verriegeltem Fehler mindestens 3 s halten; nach erfolgreichem Reset loslassen und für eine neue Freigabe erneut drücken. Ein beim Boot bereits gedrückter Taster erteilt keine Freigabe. Das Startsignal kommt anschließend lokal oder aus Home Assistant. Die Tür muss geschlossen sein; Öffnen vor oder während der Sitzung verwirft die Freigabe bzw. beendet die Sitzung ohne automatischen Neustart.

## Installation auf dem Pi

Die Erstinstallation benötigt einmaligen Zugriff auf den Pi. Aus dem übertragenen Projektverzeichnis:

```sh
sudo apt-get update
sudo apt-get upgrade -y
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
sudo sh deploy/install.sh
```

Nach Aktivierung prüfen, ob `/dev/spidev0.0` und `/dev/i2c-1` vorhanden sind; falls erforderlich neu starten. Nicht blind GPIOs oder Busse testen, während unbekannte Hardware angeschlossen ist.

Der Installer verwendet Distributionspakete und eine virtuelle Python-Umgebung mit Zugriff auf die Systempakete. Er erhält eine vorhandene Konfiguration sowie Zugangsdaten und Fehlerstatus. Der Dienst läuft als installierender Pi-Benutzer, nicht als root. Das ist beim Erstaufbau normalerweise `alpha`; dieser Benutzer benötigt Zugriff auf `gpio`, `spi`, `i2c`.

Auf dem Pi:

```sh
saunactl status
saunactl stop
journalctl -u sauna-controller -n 40 --no-pager
systemctl status sauna-controller
```

Konfiguration: `/etc/sauna/controller.toml`. Zugangsdaten: `/etc/sauna/mqtt.env`. Anwendung: `/opt/sauna/src`. Fehlerzustand: `/var/lib/sauna/fault.json`. Bediensocket: `/run/sauna/control.sock`, nur für den Dienstbenutzer zugänglich.

## Home Assistant

Der konfigurierte Broker ist `192.168.2.49:1883`. Falls Mosquitto Authentifizierung verlangt, muss der Benutzer seine bestehenden MQTT-Zugangsdaten direkt auf dem Pi in `/etc/sauna/mqtt.env` eintragen:

```text
MQTT_USERNAME="bestehender-mqtt-benutzer"
MQTT_PASSWORD="bestehendes-mqtt-passwort"
```

Die Datei ist nicht Teil eines Releases und darf nicht ins Repository. Bei Sonderzeichen die Regeln für systemd `EnvironmentFile` beachten. Danach `sudo systemctl restart sauna-controller` ausführen. Für einen unverschlüsselten lokalen Broker ist `tls = false` voreingestellt. Wenn der Broker TLS anbietet, `tls = true`, Port und gegebenenfalls `ca_file` passend setzen; Zertifikatsprüfung bleibt aktiv.

Home Assistant benötigt eine funktionierende MQTT-Integration mit aktivierter Discovery. Nach erfolgreicher Anmeldung erscheint ein Gerät „Sauna Controller“ mit den Entitäten. Die Climate-Entität zeigt PT100-Ist-/Solltemperatur. Banktemperatur und Feuchte erscheinen separat. Topic-Basis ist `sauna/saunapi`. Discovery liegt unter `homeassistant/…/saunapi/…/config`.

Der Brokerbenutzer braucht Publish-Rechte auf die Sauna-Zustände und die Discovery-Topics sowie Subscribe-Rechte auf `sauna/saunapi/command/+` und `homeassistant/status`. Commands sind nicht retained; MQTT-Verbindungen haben keine dauerhafte Command-Session. Retained Befehle und Befehle außerhalb ihrer lokalen Freigabe werden abgewiesen.

Bei MQTT-Ausfall läuft eine bereits gestartete Sitzung bis zum bestehenden lokalen Fristende weiter. Der Timer wird durch Netzwerkunterbrechung, erneuten HEAT-Befehl oder HA-Neustart nicht verlängert. Nach Prozess- oder Pi-Neustart bleibt die Heizung immer AUS. Ein unabhängiger lokaler Abschaltweg bleibt erforderlich.

## Anschluss und Updates

Siehe [GPIO-Belegung und elektrische Schnittstellen](docs/ANSCHLUSS.md), [Updatekonzept](docs/UPDATES.md) und [ausführlicher Implementierungsplan](IMPLEMENTIERUNGSPLAN.md).

Ein Softwaretest bestätigt keine elektrische Sicherheit. Ein unabhängiger STB mit eigenem Fühler, geeignete Hardwareüberwachung/Freigabe und fachgerecht ausgelegte Leistungspfade bleiben erforderlich. Ein gehängter Prozess kann GPIOs aktiv lassen; der externe Watchdog muss deshalb unabhängig abschalten können. Ein systemd-Neustart allein reicht dafür nicht.
