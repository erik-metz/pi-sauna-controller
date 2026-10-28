# Updates über GitHub und systemd

Implementiert ab v0.2.0. Repository: `erik-metz/pi-sauna-controller`. Den Git-Push und die Veröffentlichung des ersten Releases führt der Benutzer aus. Vorher kann der Pi keine GitHub-Version finden.

## Ablauf

`sauna-updater.timer` startet etwa zwei Minuten nach dem Boot und dann alle 15 Minuten `sauna-updater.service`. Der Dienst prüft das neueste veröffentlichte stabile GitHub-Release. Ein Push auf `main` aktualisiert noch kein Gerät.

Er lädt `manifest.json`, `manifest.sig` und `sauna-controller.tar.gz`. Vor dem Austausch prüft er RSA/SHA-256-Signatur, Versionsnummer, Paket-Hash, Größen und Archivpfade. Der öffentliche Prüfschlüssel wird bei der Erstinstallation auf dem Pi verankert. Für das öffentliche Repository braucht der Pi kein GitHub-Token.

Der Hauptregelkreis muss anschließend atomar einen Wartungszustand bestätigen. Bei laufender Sitzung, lokaler Startfreigabe, verriegelter Störung oder nicht bestätigten abgefallenen Schützen wird das Update verschoben. Ein externer Blick auf „OFF“ allein reicht nicht.

Erst nach der Startsperre stoppt der Updater den Controller und tauscht den Symlink auf die neue Version aus. Die neue Version muss mit korrekter Versionsmeldung, ohne Fehler und ohne Heizanforderung im Wartungszustand starten. Danach wird die Sperre aufgehoben. Die Heizung bleibt OFF und braucht eine neue bewusste Freigabe.

Bei Fehlstart erfolgt ein Rücksprung auf die vorherige Version, ebenfalls OFF. Fehlgeschlagene Versionen werden nicht endlos erneut versucht. Wenn auch die vorherige Version nicht gesund startet, bleibt die Wartungssperre bestehen. Fehler werden nie automatisch quittiert.

Eine persistente Transaktionsdatei sperrt Starts auch nach Stromausfall mitten im Update. Beim nächsten Lauf wird zunächst die vorherige Version wiederhergestellt, bevor neue Releases geprüft werden.

## Dateien auf dem Pi

| Pfad | Inhalt |
|---|---|
| `/opt/sauna/releases/<version>/src` | Root-eigener Anwendungscode |
| `/opt/sauna/current` | Symlink auf aktive Version |
| `/opt/sauna/venv` | Python mit Distributionspaketen |
| `/opt/sauna/updater/updater.py` | Root-eigener Bootstrap-Updater |
| `/etc/sauna/runtime.env` | MQTT-/Repository-Variablen, Modus 0600 |
| `/etc/sauna/controller.toml` | Hardware- und Regelparameter |
| `/etc/sauna/release-public.pem` | Öffentlicher Prüfschlüssel |
| `/etc/sauna/updater.toml` | Updater-Konfiguration |
| `/var/lib/sauna/fault.json` | Verriegelte Controllerfehler |
| `/var/lib/sauna-updater/maintenance.json` | Dauerhafte Updatesperre/Transaktion |

Der Controller läuft als normaler Pi-Benutzer. Der Updater benötigt root zum Austausch der Dateien und zur systemd-Bedienung. Die Konfigurationsprüfung der neuen Anwendung läuft mit den Rechten des Controller-Benutzers. Automatisch aktualisiert wird nur Anwendungscode; Updater, Systemdienste, Bibliotheken und Hardwarekonfiguration brauchen eine bewusste administrative Änderung.

## Lokale .env und Geheimnisse

Die echte `.env` ist ignoriert und muss separat gesichert werden. `.env.example` enthält nur Platzhalter. Lokale Variablen sind `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS`, `MQTT_CA_FILE` und `GITHUB_REPOSITORY`.

`RELEASE_SIGNING_KEY_B64` enthält den privaten RSA-Signierschlüssel als einzeiligen Base64-Wert. Base64 ist keine Verschlüsselung: Die gesamte `.env` ist geheim und gehört in eine verschlüsselte Sicherung außerhalb von Git. Ein eventuell vorhandenes `.release-secrets/` enthält ebenfalls privates Material und wird ignoriert. Die `.env` enthält bereits eine vollständige Sicherung des erzeugten Schlüssels.

Der Installer exportiert nur eine feste Liste der Laufzeitvariablen in `runtime.env`. Der private Signierschlüssel wird niemals auf den Pi übertragen. `deploy/release-public.pem` ist dagegen öffentlich und muss versioniert werden. Ein Schlüsseltausch auf dem Pi ist ein administrativer Vorgang.

## GitHub und Releases

1. Code, `.env.example` und öffentlichen Schlüssel pushen. Die echte `.env` und `.release-secrets/` müssen von Git ignoriert sein.
2. In den Repository-Einstellungen unter Actions Secrets ein Secret namens `RELEASE_SIGNING_KEY_B64` anlegen und nur diesen Wert aus der lokalen `.env` übernehmen. MQTT-Zugangsdaten bleiben lokal; die komplette `.env` wird nicht hochgeladen.
3. Version in `src/sauna_controller/__init__.py` und `pyproject.toml` gemeinsam erhöhen, Tests ausführen und committen.
4. Passenden Tag erstellen und pushen, z. B. `git tag v0.2.0` und `git push origin v0.2.0`.
5. Der Workflow testet, baut, signiert, verifiziert und veröffentlicht die drei Update-Dateien.

Alternativ erzeugt `python3 tools/build_release.py` lokal ein signiertes Paket aus der ignorierten `.env`. Die drei Dateien können als passendes GitHub-Release veröffentlicht werden. Das signierte Manifest enthält die vollständige Commit-ID.

## Diagnose

```sh
systemctl list-timers sauna-updater.timer
sudo systemctl start sauna-updater.service
journalctl -u sauna-updater.service -n 50 --no-pager
saunactl status
```

Download- oder Signaturfehler verändern die laufende Steuerung nicht. Wartungs- und Fehlerdateien nicht blind löschen, um eine Störung zu umgehen. Bei fehlgeschlagenem Rücksprung zuerst Logs, Konfiguration, Sensoren und Schützrückmeldungen prüfen. Die unabhängige Hardwareabschaltung bleibt bei jedem Softwarestand erforderlich.
