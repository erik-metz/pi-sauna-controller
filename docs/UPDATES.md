# Versionsverteilung und Updates

Stand 28.10.2026. Noch kein Repository veröffentlicht, kein GHCR-Image gebaut und kein automatischer Updater installiert.

## Entscheidung für den vorhandenen Pi

Der echte Pi meldet `armv6l`. Docker unterstützt die ursprünglichen Pi Zero/Zero W mit aktuellen offiziellen Engine-Paketen nicht mehr. Zusätzlich endet die offizielle Raspberry-Pi-OS-32-Bit-Paketlinie bei Engine 28. Ein ARMv6-Containerimage allein löst die fehlende aktuelle Engine-Unterstützung nicht. [Docker-Dokumentation](https://docs.docker.com/engine/install/raspberry-pi-os/)

Das ursprüngliche `containrrr/watchtower` wurde am 17.12.2025 archiviert. Für dieses neue Projekt wird es deshalb nicht als Updatebasis eingeplant. Unabhängig vom gewählten Nachfolger darf ein generischer Container-Updater keine laufende Heizsitzung unterbrechen und neu starten. [Watchtower-Projekt](https://github.com/containrrr/watchtower/releases)

Für den vorhandenen Pi ist ein nativer systemd-Dienst mit versionierten GitHub-Releases die bevorzugte Lösung. Damit lässt sich derselbe Komfort eines vom Pi selbst abgeholten Updates erreichen. Docker ist für automatisches Herunterladen nicht erforderlich. Die erste Einrichtung auf dem Pi braucht bei beiden Varianten einmaligen administrativen Zugriff; auch SPI/I²C müssen auf dem Host aktiviert werden.

## Vorgesehener Ablauf eines späteren automatischen Updates

1. CI führt Softwaretests aus und baut ein fest versioniertes Release. Nur ausdrücklich freigegebene Releases gelangen in den Gerätekanel; kein beliebiger Push auf `main` löst einen Austausch aus.
2. Der Pi prüft auf eine neue freigegebene Version und lädt sie in ein Staging-Verzeichnis. Netzwerkausfall verändert den laufenden Controller nicht.
3. Herkunft und Integrität werden über einen vorab verankerten Signaturschlüssel oder eine gleichwertige verifizierte Release-Signatur geprüft. Eine Prüfsumme allein weist keine vertrauenswürdige Herkunft nach.
4. Der Controller wechselt atomar in einen Wartungszustand: keine aktive Sitzung, keine Freigabe, Heizanforderung AUS, beide Spiegelkontakte geschlossen, keine unaufgelöste Störung. **Ein einzelnes externes Lesen von „OFF“ reicht nicht**, da zwischen Prüfung und Update ein neuer Start eintreffen könnte.
5. Der Wartungszustand sperrt neue Starts und entzieht die Hardwarefreigabe. Erst jetzt beendet der Updater den Dienst und schaltet auf die neue Version um.
6. Konfiguration, Zugangsdaten und verriegelte Fehler bleiben außerhalb des Codes erhalten. Die alte Version bleibt für einen Rücksprung verfügbar. Persistente Datenformate müssen rückwärtskompatibel sein oder vor einer Migration gesichert werden.
7. Die neue Version startet ausschließlich OFF. Ein Funktionstest prüft Dienst, Konfiguration, Zustandsausgabe und Schnittstellen. Bei Fehlern Rücksprung auf die alte Version, ebenfalls OFF; nie eine Saunasitzung automatisch fortsetzen.

Dieser Wartungshandshake und der automatische Updater sind ein eigener nächster Implementierungsschritt und in v0.1 **noch nicht enthalten**. Der vorhandene Installer ist für bewusst ausgelöste Erstinstallation bzw. Wartung gedacht, nicht für unbeaufsichtigte periodische Ausführung während des Betriebs.

## Option auf neuerem 64-Bit-Pi

Auf einem geeigneten 64-Bit-Pi kann CI denselben Code in ein Image bauen und nach GHCR veröffentlichen. Der Pi zieht das Image, aber ein Hostdienst entscheidet anhand des Wartungshandshakes über den Austausch. Nur die Controller-Anwendung muss als Container laufen; der kleine Host-Updater kann systemd verwenden.

Version und Image-Digest festhalten; Konfiguration, Zugangsdaten und Fehlerzustand persistent außerhalb des Images speichern. GPIO-/SPI-/I²C-Geräte gezielt zuordnen, kein pauschales `privileged` und kein Docker-Socket im Controller. Die externe Hardwareabschaltung bleibt auch im Container erforderlich. Ein Hostwatchdog muss außerdem den Zustand der Hauptschleife überwachen, da das bisherige systemd-Notify nicht unverändert durch jede Containerkonfiguration funktioniert.

GHCR unterstützt öffentliche anonyme Pulls; private Images benötigen passende Leseberechtigung. Ein öffentliches Image darf keine Heimnetz-Zugangsdaten enthalten. Die geplante Anwendungskonfiguration wird deshalb immer außerhalb des Images bereitgestellt. [GitHub: Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
