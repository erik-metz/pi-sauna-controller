# Implementierungsplan: Sauna-Controller mit Raspberry Pi Zero W

Stand: 28.10.2026. Planungsstand, noch keine ausführbare Heizungssteuerung und kein freigegebener Stromlaufplan.

## 1. Ziel und Entscheidungen

Der Pi regelt die Heizung autonom, überwacht Sensoren und Schützrückmeldungen und beendet jede Sitzung selbstständig. Home Assistant dient als Bedienoberfläche, Anzeige und Historie. Weder WLAN noch Home Assistant sind Teil des lokalen Regelkreises.

Vorgesehen sind:

- Raspberry Pi Zero W 1.1 außerhalb der heißen/feuchten Kabine.
- Zwei getrennte GPIO-Ausgänge zu zwei Koppelrelais, die jeweils einen Leistungsschütz ansteuern.
- Leistungskontakte beider Schütze in Reihe, über alle für den Ofen erforderlichen aktiven Leistungsleiter; Auslegung durch Elektrofachkraft.
- Je ein nachgewiesener Öffner-Spiegelkontakt als Rückmeldung.
- PT100 mit MAX31865 als primärer Regelfühler über dem Ofen, Position gemäß konkreter Ofenanleitung.
- SHT41 auf Bankhöhe für Temperatur- und Feuchteanzeige.
- Ein separat bedienbarer Lichtkanal.
- Zusätzliche unabhängige Übertemperaturabschaltung, überwachte Hardwarefreigabe und lokale Bedienmöglichkeiten.

**Wesentliche Korrektur:** Zwei Schütze verhindern kein Kontaktverschweißen. Bei einem einzelnen verschweißten Schütz soll der zweite den Heizstrom unterbrechen. Ein gemeinsamer Fehler, eine ungeeignete Rückmeldung oder zwei gleichzeitig verschweißte Schütze werden dadurch nicht ausgeschlossen. Daraus allein ergibt sich keine Sicherheitszertifizierung.

Für das endgültige Gerät sind ein unabhängiger Sicherheitstemperaturbegrenzer (STB) mit eigenem Fühler und manuellem Reset sowie eine geeignete überwachte Abschaltkette einzuplanen. Ein vorhandener STB des Ofens ist anhand der Unterlagen zu prüfen und zu erhalten. Netzanschluss, Schutzorgane, Schützauslegung und abschließende Prüfung übernimmt eine Elektrofachkraft.

## 2. Systemaufteilung

```text
Home Assistant <-- MQTT-Broker im Heimnetz --> Pi / sauna-controller
                                               |
                    +--------------------------+---------------------+
                    |                          |                     |
               PT100/MAX31865              SHT41               Lichttreiber
                    |
          lokaler Zustandsautomat
                    |
          Relais 1          Relais 2
                    |
    unabhängige Hardwarefreigabe / STB / Watchdog
                    |
            Schütz K1      Schütz K2
                    |
              Sauna-Heizleistung

Spiegelkontakte K1/K2 --> Rückführkreis der Hardwareüberwachung
                     --> elektrisch angepasste Diagnoseeingänge des Pi
```

Das ist eine Funktionsübersicht, keine Verdrahtungsanweisung. Rückführkreis und Pi-Diagnose dürfen sich elektrisch nicht beeinflussen. Die Fachplanung legt fest, ob getrennte Kontakte oder geeignete Diagnoseausgänge verwendet werden.

Der Pi darf Heizleistung anfordern. Die unabhängige Hardware muss diese Anforderung unterbrechen können, auch wenn GPIOs dauerhaft aktiv bleiben. Der STB muss beide Spulenpfade wirksam sperren können; bei einem verschweißten Leistungsschütz bleibt dadurch der andere Abschaltweg verfügbar.

## 3. Elektrische Schnittstellen und Aufbau

GPIOs arbeiten mit 3,3-V-Logik und sind nicht 5-V-tolerant. Relais- oder Schützspulen werden niemals direkt aus GPIOs versorgt. Koppelstufen müssen zum GPIO-Pegel, Spulenstrom und zur Spulenspannung passen. Für Gleichstromspulen geeignete Entstörung vorsehen; deren Einfluss auf die Abfallzeit berücksichtigen. Die 24-V-Steuerspannung ist ein möglicher Aufbau, keine bereits bestätigte Eigenschaft der vorhandenen Bauteile. [Raspberry-Pi-GPIO-Dokumentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)

Anforderungen an die Ausgänge:

- Hardwareseitig AUS bei Boot, Reset, ausgeschaltetem Pi und nicht initialisierten GPIOs.
- Externe Widerstände und passend gewählte Treiberlogik statt alleiniger Software-Pulls.
- Koppelrelais mit Arbeitskontakt: ohne Freigabe keine bestromte Schützspule.
- Kein Bootimpuls darf die Schütze anziehen lassen; mit Logikanalysator und realem Treiber prüfen.
- Ein Prozessabsturz kann einen GPIO-Pegel stehen lassen. Deshalb zusätzlich eine externe, zeitüberwachte Freigabe verwenden.

Für die Rückmeldung sind dokumentierte Spiegelkontakte erforderlich. Ein beliebiger Hilfskontakt sagt nicht verlässlich aus, dass die Hauptkontakte geöffnet haben. Ein Öffner-Spiegelkontakt darf bei einem geschlossenen Hauptkontakt nicht geschlossen sein. Die Aussage ist asymmetrisch: Ein offener Spiegelkontakt allein beweist nicht, dass der Leistungspfad geschlossen ist. [Schneider Electric: Spiegelkontakte](https://www.se.com/uk/en/faqs/FA142116/)

Eingänge elektrisch entkoppeln/anpassen, gegen Störungen schützen und mit definiertem Ruhepegel versehen. Einfache Zweidrahtkontakte erkennen nicht jeden Leitungsfehler: Ein Kurzschluss kann eine zulässige Rückmeldung vortäuschen. Leitungsüberwachung und Diagnosegrenzen müssen im Hardwarekonzept festgelegt werden.

Netzspannung und Kleinspannung räumlich trennen. Pi, Netzteile, MAX31865 und Relaisbaugruppen bleiben außerhalb der Kabine in geeigneten Gehäusen. Nur geeignete Fühler, temperaturfeste Leitungen und dafür spezifizierte Leuchten werden in die Kabine geführt. Schaltstörungen der Schütze dürfen Sensorleitungen nicht beeinflussen.

## 4. Sensorik

### PT100 und MAX31865

Bevorzugt PT100 in 4-Leitertechnik, alternativ 3-Leitertechnik; 2-Leiter nur bei akzeptiertem und dokumentiertem Leitungsfehler. Das konkrete MAX31865-Modul muss für PT100 ausgelegt sein. Tatsächlichen Referenzwiderstand, Fühlerkennlinie und Brückenkonfiguration aus den Unterlagen übernehmen, keine PT1000-Modulwerte verwenden.

SPI zum MAX31865 kurz halten, den PT100 über temperaturfeste Leitung in die Sauna führen. Filter für 50-Hz-Netzumgebung konfigurieren. Der Treiber muss neben Temperatur und Rohwiderstand auch Fehlerregister auslesen und die vorgesehenen Fault-Detection-Zyklen auslösen; nicht alle Fehler werden allein durch normales Auslesen geprüft. [Analog Devices: MAX31865-Datenblatt](https://www.analog.com/media/en/technical-documentation/data-sheets/MAX31865.pdf)

Jede Messung enthält Wert, Rohwert, lokale monotone Empfangszeit, Gültigkeit und Fehlerursache. Fehler dürfen nicht in einen scheinbar plausiblen Ersatzwert umgewandelt werden. Unterbrechung, Kurzschluss, Busfehler und überalterte Messungen führen zum Heizstopp.

Die Montageposition folgt der konkreten Ofenanleitung. „Direkt über dem Ofen“ allein ist dafür zu ungenau; insbesondere Zuluft kann den Fühler kühlen und dadurch die Regelung verfälschen. [Harvia: Fühlermontage](https://support.harvia.com/hc/en-gb/articles/27215437777948-Temperature-sensor-installation)

### SHT41

Der Sensorchip bietet I²C, Messwert-CRC und einen Betriebsbereich bis 125 °C. Das ist keine pauschale Freigabe eines Breakout-Moduls samt Steckern, Kabeln und Gehäuse für den Dauereinsatz in der Sauna. Genauigkeit und Alterung sind anhand des Datenblatts für die tatsächliche Temperatur und Feuchte zu bewerten. [Sensirion: SHT4x-Datenblatt](https://sensirion.com/resource/datasheet/sht4x)

Montage belüftet auf Bankhöhe, ohne direkte Strahlung vom Ofen und ohne direkten Aufgussstrahl; Schutz gegen Berührung und Tropfwasser, ohne den Feuchteaustausch zu blockieren. Falls der interne Sensorheizer verwendet wird, sind beeinflusste Messungen während und nach dem Heizen zu kennzeichnen und nicht als normale Raumwerte zu melden.

Mehrere Meter ungeschütztes I²C durch die Sauna werden nicht als verlässlich vorausgesetzt. Vor der Umsetzung Leitungslänge und Buskapazität prüfen. Bei ungeeigneter Strecke sind ein validierter differenzieller I²C-Extender mit passender Versorgung oder ein außerhalb der Hitze platzierter Sensorknoten mit robuster Leitungsanbindung vorzusehen. Ein niedrigerer Bustakt allein löst nicht jedes Leitungsproblem.

V1 nutzt den SHT41 ausschließlich zur Anzeige. Sein Ausfall meldet eine Warnung und macht seine Werte ungültig; die PT100-Regelung darf bei sonst fehlerfreiem Zustand weiterlaufen. Sobald Banktemperatur Regelgröße wird, muss auch deren Ausfall zum Heizstopp führen.

## 5. Bedeutung der Solltemperatur und Regelung

**V1 folgt der Vorgabe: Der Sollwert bezieht sich auf den PT100 über dem Ofen.** Ein Sollwert von beispielsweise 80 °C ist deshalb keine Zusage von 80 °C an der Sitzbank. Es gibt keinen allgemein gültigen festen Temperaturversatz zwischen beiden Orten.

In Home Assistant heißt die Klimaentität entsprechend „Sauna – Ofenfühler-Regelung“. Deren Isttemperatur ist der PT100-Wert; der SHT41 erscheint separat als „Banktemperatur“. Damit zeigt die Klimaentität nicht einen Sensor an, während sie heimlich auf einen anderen regelt.

V1 verwendet einen Zweipunktregler mit Hysterese. Bei Sollwert S und Hystereseband H:

- Unter S − H/2: Heizanforderung EIN.
- Über S + H/2: Heizanforderung AUS.
- Dazwischen: vorherige Anforderung halten.

Hysterese und Mindestschaltzeiten werden anhand der Anlage bestimmt. Für Simulationen können beispielsweise 4 K Bandbreite und 30 s Mindestpause verwendet werden. Diese Werte sind keine freigegebenen Betriebsparameter. Mechanische Schütze werden nicht mit schneller PWM betrieben.

Sicherheitsabschaltungen, Bedien-AUS und Sitzungsende umgehen jede Mindest-Einschaltzeit. Ein niedrigerer Sollwert muss eine erforderliche Abschaltung sofort ermöglichen. Temperaturgrenzen werden zusätzlich am ungeglätteten gültigen Messwert geprüft; Glättung darf eine Überschreitung nicht verbergen. Ein konstanter Wert allein beweist keinen defekten Fühler. Für Plausibilitätsprüfungen werden außerdem Temperaturentwicklung, Heizanforderung und Zeit betrachtet.

Soll später „80 °C an der Bank“ einstellbar sein, wird das eine ausdrücklich andere Betriebsart: entweder Bankregelung mit PT100-Begrenzung oder eine langsame Bankregelung, die den Sollwert des PT100-Reglers innerhalb fester Grenzen nachführt. Erst nach Eignungsprüfung des Bankfühlers und Messfahrten implementieren. Feuchte bleibt reine Anzeige; eine aktive Feuchteregelung ist ohne Verdampfer nicht vorgesehen.

## 6. Schützsteuerung und Rückmeldung

V1 schaltet beide Schütze bei jedem Heizzyklus gemeinsam ein und aus. Das erhöht die Schaltspielzahl gegenüber einem ständig angezogenen Freigabeschütz, vereinfacht aber die regelmäßige Prüfung beider Abschaltpfade. Lebensdauer und zulässige Schaltfrequenz sind vorab zu dimensionieren.

| Situation | Erwartete Spiegelkontakt-Rückmeldung | Reaktion |
|---|---|---|
| Startprüfung, beide Ausgänge AUS | Beide Öffner geschlossen | Erst dann Start zulassen |
| Beide Ausgänge EIN | Beide Öffner innerhalb der Anzugsfrist offen | Sonst beide AUS, Fehler verriegeln |
| Reguläre Heizpause oder Stopp | Beide Öffner innerhalb der Abfallfrist geschlossen | Sonst beide AUS, Fehler verriegeln |
| Ungeplante Änderung während stabiler Anforderung | Rückmeldung widerspricht Erwartung | Beide AUS, Fehler verriegeln |

Anzugs-/Abfallfristen berücksichtigen Koppelrelais, Schütz, Entstörung, Eingangsentprellung und Linux-Jitter. Zunächst aus Datenblättern ableiten und im Aufbau nachmessen. Diagnoseabfrage beispielsweise alle 50–100 ms; das ist eine Software-Zielgröße, keine garantierte Sicherheitsreaktionszeit.

Ein festgehaltener Schütz oder ein klebendes Koppelrelais darf keinen erneuten Start zulassen. Bei einer Abweichung werden immer beide Anforderungen zurückgenommen. Eine Fehlerquittierung allein startet nicht erneut.

Einzeltests der Schütze erfolgen im Prüfaufbau ohne angeschlossene Heizlast. Im regulären Bootvorgang gibt es keine automatischen Testimpulse auf die Heizung. Optional kann später eine unabhängige Strommessung die Diagnose verbessern; sie ersetzt weder Spiegelkontakte noch STB.

## 7. Unabhängige Freigabe und Fernstart

Vorgesehen ist eine Hardwareüberwachung, die bei fehlenden gültigen Lebenszeichen die Spulenfreigabe entzieht. Das Lebenszeichen darf nur nach einem erfolgreich abgeschlossenen Regel-/Überwachungsdurchlauf erzeugt werden. Ein unabhängiger Thread oder Hardware-PWM, der bei blockiertem Regler weiterläuft, wäre ungeeignet. Timeout und sichere Reaktion sind messtechnisch nachzuweisen.

Diese Watchdogfunktion fängt bestimmte Stillstände ab. Sie erkennt nicht jeden Softwarefehler, beispielsweise einen logisch falschen, aber weiterlaufenden Regler. STB und unabhängige Laufzeitbegrenzung bleiben erforderlich. Das konkrete Sicherheitsmodul und die Architektur werden fachlich ausgewählt; ein beliebiger Watchdog-Bausatz ist keine belegte Sicherheitssteuerung.

Für den ersten Heizbetrieb wird eine lokale bewusste Freigabe vorgesehen. Unbeaufsichtigter Start nur über Home Assistant wird erst nach Klärung einer zum Ofen passenden Fernstartabsicherung zugelassen. Hersteller setzen dafür beispielsweise überwachte Türfreigaben oder Ofenabdeckungs-Sicherheitseinrichtungen ein. Ein Türkontakt allein ist noch kein vollständiges Fernstartkonzept. [Harvia: Sicherheitseinrichtungen](https://support.harvia.com/hc/en-gb/articles/27609860337308-Safety-devices-for-electric-heaters)

Geplantes Bedienmodell:

1. Kabine und Ofen lokal prüfen, bewusste Freigabe erteilen.
2. Freigabe nur zeitlich begrenzt und für eine Sitzung gültig.
3. Eine Türöffnung vor dem Start verwirft die Freigabe; bloßes Schließen erteilt sie nicht erneut.
4. Start verlangt fehlerfreie Sensoren, plausible Rückmeldungen und geschlossene Hardwarefreigabe.
5. Nach Ende, Fehler oder Neustart ist eine neue bewusste Freigabe nötig.

Türöffnung während des Betriebs und örtliche STOP-Bedienung werden ausdrücklich im späteren Bedien-/Sicherheitskonzept festgelegt. Eine lokale Hardwareabschaltung muss unabhängig von WLAN und Pi-Prozess wirken.

## 8. Zustandsautomat, Laufzeit und Fehlerverhalten

| Zustand | Heizanforderung | Verhalten |
|---|---|---|
| BOOT / SELFTEST | AUS | Ausgänge sicher initialisieren, Konfiguration/Sensoren/Rückmeldungen prüfen |
| OFF | AUS | Messwerte melden, Licht separat bedienbar |
| ARMED | AUS | Gültige lokale Freigabe, auf expliziten Start warten |
| PREHEAT | nach Regler | Sitzung läuft, Ziel wird angefahren |
| HOLD | nach Regler | Sollbereich erreicht, lokal weiterregeln |
| STOPPING | AUS | Öffnen beider Schütze prüfen |
| FAULT | AUS | Ursache speichern, kein automatischer Wiederanlauf |

PREHEAT und HOLD sind Sitzungsphasen, nicht gleichbedeutend mit tatsächlicher Heizleistung. HOLD kann bei abfallender Temperatur wieder heizen. Eine optionale Abkühlanzeige ist rein informativ: Es gibt keinen Ventilator oder sonstige aktive Kühlung.

Zeitfunktionen verwenden eine monotone Uhr. Netzzeitkorrekturen dürfen Laufzeitbegrenzungen nicht verändern. Eine maximale Gesamtsitzungsdauer beginnt mit Annahme des Starts einschließlich Aufheizen; Haltezeit und Profilphasen setzen diese Frist nicht zurück. Zusätzlich maximale Aufheizdauer und unabhängige Hardware-Laufzeitbegrenzung vorsehen. Wiederholtes START während einer Sitzung erneuert die Frist nicht.

| Ereignis | Geplantes V1-Verhalten |
|---|---|
| PT100 ungültig, MAX-Fault oder Messung zu alt | Beide AUS, verriegelter Fehler |
| Software-Temperaturgrenze überschritten | Beide AUS, verriegelter Fehler; STB bleibt unabhängig |
| Schützrückmeldung unplausibel | Beide AUS, verriegelter Fehler |
| STB / Hardwarefreigabe ausgelöst | Heizpfade hardwareseitig gesperrt, Pi meldet Fehler |
| SHT41 ungültig | Warnung, Bankwerte ungültig; kein Ersatz durch alte Werte |
| WLAN/MQTT/Broker fällt aus | Begonnene Sitzung lokal bis höchstens bestehendem Fristende fortführen; keine neuen Netzwerkstarts |
| Pi-Neustart oder Prozessneustart | Immer OFF, laufende Sitzung nicht wiederaufnehmen |
| SD-/Konfigurationsfehler | Keine neue Freigabe; bei sicherheitsrelevantem Laufzeitfehler Sitzung stoppen |
| Ungültiger oder veralteter Befehl | Ablehnen und Grund melden |

Die Entscheidung zum Netzwerkausfall setzt lokale Abschaltung und alle unabhängigen Begrenzungen voraus. Vor Inbetriebnahme kann alternativ „Stopp bei Kommunikationsverlust“ festgelegt werden. Für V1 bleibt die obige, eindeutig getestete Regel maßgebend.

Verriegelte Fehler bleiben über Prozessneustarts erhalten. Kritischer Fehlerstatus wird atomar gespeichert; ist der Speicherzustand unklar, bleibt das Gerät gesperrt. Hardwareauslösungen werden lokal zurückgesetzt. Eine spätere Rückkehr plausibler Messwerte hebt einen Fehler nicht automatisch auf.

## 9. Lokale Programme

Programme bestehen aus auf dem Pi gespeicherten Phasen: Zieltemperatur am PT100, maximale Aufheizzeit, Haltezeit und optional zulässige Rampe. Beispielnamen sind „Sanft“, „Standard“ und „Heiß“; konkrete Temperaturen werden erst nach der Anlagencharakterisierung festgelegt.

Die Haltezeit beginnt erst, wenn das definierte Sollband für eine konfigurierte Bestätigungszeit erreicht ist. Für V1 zählt danach verstrichene Haltezeit; längere Abweichung vom Band wird gemeldet und gegebenenfalls als Aufheiz-/Regelfehler beendet. Die Gesamtsitzungsfrist hat stets Vorrang. Am Ende wird ausgeschaltet, auch wenn HA nicht erreichbar ist.

Sollwertänderungen werden lokal begrenzt. Profiländerungen während des Betriebs sind für V1 gesperrt; ein neues Profil erfordert eine neue Sitzung. Keine automatische Wiederaufnahme nach Stromausfall und kein unbegrenztes Warmhalten. Kalenderstarts werden zunächst nicht implementiert.

## 10. Home Assistant über MQTT

Der Broker läuft auf dem bestehenden Server oder einem anderen zuverlässigen Heimnetzgerät. Auf dem Pi läuft nur der MQTT-Client neben der lokalen Steuerung. MQTT Discovery gruppiert die Entitäten zu einem Gerät. Home Assistant unterstützt hierfür unter anderem Geräteerkennung, eindeutige IDs und Verfügbarkeit über Birth/Last-Will-Nachrichten. [Home Assistant: MQTT](https://www.home-assistant.io/integrations/mqtt/)

Geplante Entitäten:

| Entität | Inhalt |
|---|---|
| Climate | OFF/HEAT, PT100-Sollwert, PT100-Istwert, Aktion off/idle/heating |
| Sensor | Banktemperatur und relative Feuchte |
| Sensoren | Sitzungsphase, Restlaufzeit, letzter Fehler, Messwertalter |
| Binary Sensor | Freigabe, Störung, Hardwarekette, Rückmeldungen K1/K2 |
| Light | Banklicht EIN/AUS, optional später Helligkeit |
| Select | Lokales Programm |
| Button | Sitzung stoppen; Start nur im gültig freigegebenen Zustand |

„heating“ bezeichnet eine angeforderte und rückgemeldete Schützstellung, keine unabhängig gemessene Leistungsaufnahme. Bei Diagnosefehlern wird der Fehlerstatus sichtbar; ohne Stromsensor wird kein gemessener Energieverbrauch behauptet.

Topics beispielsweise:

```text
sauna/<device_id>/availability
sauna/<device_id>/state
sauna/<device_id>/telemetry
sauna/<device_id>/command/mode
sauna/<device_id>/command/target
sauna/<device_id>/command/stop
sauna/<device_id>/command/light
sauna/<device_id>/event
```

Discovery darf retained sein; Steuerbefehle werden nicht retained gesendet. Retained Startbefehle werden abgewiesen, Altbestände bei Installation entfernt. MQTT-Verbindungen verwenden keine dauerhafte Warteschlange für Startbefehle. QoS-1-Duplikate müssen folgenlos bleiben: Ein wiederholtes HEAT während einer Sitzung startet keine zweite Sitzung und verlängert keine Frist.

Für klassische Climate-Befehle ohne Befehls-ID ist HEAT nur im Zustand ARMED zulässig. Eine Freigabe wird beim Start verbraucht und nach Stopp nicht automatisch erneuert. Erweiterte Programmstarts erhalten Befehls-ID und sitzungsbezogene Freigabekennung. Wiederverbindung allein löst nie einen Start aus. Zeitablauf und Ablehnung alter Befehle werden durch Tests nachgewiesen.

Die Climate-Konfiguration setzt explizite Temperaturgrenzen, Auflösung, Modi und Zustandstopics. HA zeigt bestätigte Zustände statt optimistisch angenommener Schaltvorgänge. [Home Assistant: MQTT Climate](https://www.home-assistant.io/integrations/climate.mqtt/)

Telemetrie etwa alle 5 s, Statusänderungen sofort. Last Will meldet Kommunikationsausfall; zusätzlich Messwertalter/Timeout überwachen, damit alte Daten nicht als aktuell erscheinen. Ein eigener Brokerbenutzer erhält nur benötigte Topic-Rechte. Kein Internet-Portforwarding; Zugangsdaten werden außerhalb des Quellcodes gespeichert.

## 11. Empfehlung für das Banklicht

**Bevorzugt ein geeignetes Konstantspannungs-SELV-Netzteil außerhalb der Kabine und ein passender MOSFET-LED-Controller auf der 12-/24-V-Seite.** Zuerst Spannung, Leistung und Art der vorhandenen LEDs feststellen. Bei einem Konstantstromsystem gilt diese Empfehlung nicht unverändert.

Vorteile sind leises Schalten und eine spätere Dimmfunktion. Nachteile sind Netzteil-Standbyverbrauch sowie die nötige Auslegung des DC-Treibers auf Strom, Erwärmung und Leitungen. PWM-Dimmen muss für Netzteil und LED-System vorgesehen sein; vorzugsweise einen dokumentiert kompatiblen Treiber bzw. dessen DIM-/Enable-Eingang nutzen. Auch Hersteller unterscheiden ausdrücklich zwischen Konstantstrom- und Konstantspannungs-LED-Systemen. [MEAN WELL: LED-FAQ](https://led.meanwell.com/qa.aspx?c=10)

Ein mechanisches Relais auf der DC-Seite ist für reines EIN/AUS möglich, benötigt aber eine passende DC-Schaltstromfreigabe; eine AC-Aufschrift reicht nicht. Ein primärseitiges 230-V-Relais kann den Standbyverbrauch reduzieren, muss aber speziell den Einschaltstrom des Netzteils vertragen. Netzseite bevorzugen, wenn das konkrete Netzteil keine Lastschaltung zulässt oder Standbyfreiheit gewünscht ist. Keine pauschale Sekundärschaltung beliebiger LED-Treiber.

Leuchte/LED-Band muss für die gemessene Temperatur hinter der Bank geeignet sein; Wasserschutz allein sagt dazu nichts. Eigene Sicherung auf der Kleinspannungsseite und passende Leitungsdimensionierung vorsehen. Das Licht bleibt bei Heizstopp separat bedienbar; bei Pi-Ausfall ist seine Funktion nicht garantiert. Ein vorhandenes unabhängiges Raumlicht bleibt deshalb sinnvoll.

## 12. Softwarestruktur und Betriebssystem

Der Zero W hat einen 32-Bit-ARM11 und 512 MB RAM; eine schlanke Python-Anwendung ist dafür eine passende Planungsentscheidung. Vorgesehen ist Raspberry Pi OS Lite 32-Bit ohne Desktop und ohne Containerpflicht. Paketinstallation und Treiber werden ausdrücklich auf dem tatsächlichen ARMv6-Gerät getestet. [Raspberry Pi: Zero-Modelle](https://www.raspberrypi.com/documentation/computers/zero.html), [offizielle OS-Auswahl](https://www.raspberrypi.com/software/operating-systems/)

Vorgesehene Struktur:

```text
src/sauna_controller/
  main.py                 Initialisierung und Lebenszyklus
  config.py               Schema und Konfigurationsprüfung
  models.py               Messungen, Befehle, Zustände
  controller.py           deterministischer Zustandsautomat
  thermostat.py           Hysterese und Schaltzeitbegrenzung
  safety.py               Softwaregrenzen und Fehlerverriegelung
  contactors.py           Anforderung, Rückmeldungen, Fristen
  programs.py             lokale Programmphasen und Sitzungsfristen
  hardware/               GPIO-, MAX31865-, SHT41-, Lichtadapter
  mqtt/                   Discovery, Zustände und Befehlsvalidierung
  persistence.py          atomare Konfiguration/Fehlerzustände
  simulation.py           Hardwareersatz und simulierte Zeit
tests/                    Regelung, Fehlerfälle, MQTT und Integration
config/                   dokumentierte Beispielkonfiguration
deploy/                   systemd-Dienst und Installationsanleitung
docs/                     Hardwarevertrag und Prüfprotokolle
```

Treiberkandidaten sind GPIO Zero mit zum installierten OS passendem Backend, SPI/I²C-Bibliotheken und Paho MQTT. Die Auswahl wird erst nach ARMv6-, Timeout- und Fehlerregisterprüfung festgeschrieben. Eine Bibliothek, die Fehler verschluckt oder dauerhaft blockieren kann, erfüllt den Hardwarevertrag nicht.

Nur der Controller besitzt Schreibzugriff auf Heizaktoren. MQTT übersetzt Nachrichten in validierte Befehle und schreibt niemals direkt GPIOs. Der Regelkern ist ohne Hardware testbar. Sensorzugriffe erhalten begrenzte Laufzeiten; neue Messungen kommen über begrenzte Queues/Snapshots. Ausbleibende Messungen werden anhand ihres Alters erkannt. Netzwerkprobleme dürfen den Regelzyklus nicht blockieren.

systemd startet den Dienst nach jedem Boot, aber immer mit Heizstatus OFF. Dienstneustart, Hardwarewatchdog und optional Linux-Watchdog werden aufeinander abgestimmt; ein Neustart ersetzt keine sichere Abschaltung. Dedizierter Benutzer, minimale Geräteberechtigungen, begrenzte Logs und kein automatisches Update während einer Sitzung. Statische Konfiguration und Fehlerzustände persistent speichern, schnelle Telemetrie nicht fortlaufend auf SD schreiben.

Der Hardwaremodus bleibt zunächst gesperrt; standardmäßig startet die Simulation. Ohne ausdrücklich geprüfte Sicherheitskonfiguration und Inbetriebnahmefreigabe darf keine Heizlast angesteuert werden.

## 13. Vorläufiger GPIO-Plan

BCM-Nummern und physische Pins sind getrennt angegeben. Die Zuordnung ist ein Reservierungsvorschlag und wird vor Verkabelung am konkreten Board geprüft.

| Funktion | BCM | Physischer Pin |
|---|---:|---:|
| SHT41 SDA / SCL | 2 / 3 | 3 / 5 |
| MAX31865 MOSI / MISO / SCLK | 10 / 9 / 11 | 19 / 21 / 23 |
| MAX31865 CS, SPI0 CE0 | 8 | 24 |
| Koppelrelais K1 / K2 | 17 / 27 | 11 / 13 |
| Spiegelkontakt K1 / K2, angepasste Eingänge | 22 / 23 | 15 / 16 |
| Lichttreiber | 18 | 12 |
| Externes Watchdog-Lebenszeichen | 24 | 18 |
| Diagnose Hardwarefreigabe | 25 | 22 |
| Lokale Freigabetaste | 5 | 29 |
| Tür-/Fernstartdiagnose, falls vorgesehen | 6 | 31 |

STB und lokale Hardwareabschaltung sind keine bloßen GPIO-Funktionen. Zusätzliche Diagnoseeingänge werden nach Auswahl der Sicherheitsbaugruppen reserviert. Pegel und Polarität werden pro Kanal konfiguriert und getestet.

## 14. Umsetzung in Etappen und Abnahmekriterien

### A. Hardwarevertrag und Sicherheitskonzept

Ofenunterlagen, Leistung/Phasen, vorhandenen STB, Schützmodelle samt Spiegelkontakten, Spulenspannungen, Koppelrelais, Leitungslängen und Lichtdaten erfassen. Sicherheitskette, Fernstartmodell und Grenzwerte durch Fachplanung festlegen. Ergebnis: geprüfte Funktionsbeschreibung, Stückliste und Anschlussplan. Softwarearbeit in Simulation kann parallel zu diesen Klärungen beginnen.

### B. Simulationsfähiger Regelkern

Zustandsautomat, Hysterese, Laufzeiten, Programme, Fehlerverriegelung und simulierte Uhr implementieren. Abnahme: nach jedem Neustart OFF; Fehler haben Vorrang vor Heizanforderung; keine ungültige Konfiguration startet; Zeitänderungen und doppelte Befehle verlängern keine Sitzung.

### C. Sensoren und reine Kleinspannungsprüfung

PT100/MAX31865 und SHT41 am Pi betreiben, mit Referenz vergleichen und Sensorfehler gezielt injizieren. Koppelrelais zunächst mit Prüflampen oder Logikeingängen statt Ofen betreiben. Leitungsstörungen, blockiertes I²C und SPI-Ausfälle prüfen. Abnahme: keine stillschweigend alten Werte, keine Bootimpulse, reproduzierbare Abschaltung bei Verlust des Regelfühlers.

### D. Schützdiagnose und externe Abschaltung ohne Heizlast

Beide Schütze mit realen Spulen und Rückmeldungen testen. Fehler simulieren: Rückmeldung offen/kurzgeschlossen, Relais dauerhaft EIN, Schütz fällt nicht ab, Prozess killen, Prozess anhalten, Stromausfall und Pi-Reset. Externe Freigabe separat testen. Abnahme: bei einem ausgefallenen Schaltkanal unterbricht der andere; Fehler verhindert Neustart; Watchdogabschaltung funktioniert auch bei stehen gebliebenen GPIOs.

### E. MQTT und Home Assistant

Discovery, Climate, Sensoren und Licht einbinden. Netzwerkunterbrechung, HA-/Broker-Neustart, retained Altbefehle, Duplikate und ungültige Payloads testen. Abnahme: kein Start durch Wiederverbindung, keine verlorene Laufzeitbegrenzung, bestätigte Zustände und verständliche Fehlermeldungen.

### F. Fachgerechte Inbetriebnahme mit Ofen

Erst nach elektrischer Prüfung und dokumentierter Funktion der unabhängigen Abschaltkette. Beaufsichtigte Messfahrten mit Referenzmessung: Temperaturgradienten, Überschwingen, Nachheizen nach Abschalten, Banktemperatur und Verhalten beim Aufguss erfassen. Hysterese, Schaltpausen und zulässige Sollwerte daraus ableiten. STB-Funktionsprüfung nach Herstellervorgabe durch die Fachkraft, nicht durch unkontrolliertes Überheizen der Kabine.

Abnahme: Protokollierte Grenzen und Reaktionszeiten, erfolgreicher Einzelfehlertest, verständliche lokale Bedienung und keine automatische Wiederaufnahme nach Fehler oder Versorgungsausfall. Erst danach Profile für den Alltag freigeben.

## 15. Noch benötigte Angaben

Vor einer endgültigen Hardwareauslegung und produktivem Heizcode müssen folgende Punkte geklärt werden:

1. Ofenhersteller, Modell, Nennleistung, Anschlussart und vorhandene Steuerung/STB.
2. Exakte Schütz- und Koppelrelaistypen, Spulenspannungen und Art der Hilfskontakte.
3. PT100 in 2-/3-/4-Leitertechnik, Fühler-/Kabeltemperaturfreigabe und MAX31865-Modul.
4. SHT41-Modul, Entfernung zum Pi und geplante Leitungsführung.
5. Soll sich eine spätere Wunschtemperatur auf den Ofenfühler oder ausdrücklich auf die Sitzbank beziehen?
6. Gewünschte maximale Sitzungsdauer und lokale Bedienmöglichkeiten.
7. Ist ein Start ausschließlich nach lokaler Freigabe ausreichend oder wird unbeaufsichtigter Fernstart gewünscht?
8. LED-Spannung, Leistung, Netzteilmodell und Dimmwunsch.
9. MQTT-Broker im Heimnetz bereits vorhanden, und wie wird Home Assistant betrieben?

Die konkrete nächste Implementierung ist Etappe B: ein vollständig testbarer, standardmäßig simulierter Python-Regelkern mit dokumentiertem Hardwarevertrag. Die noch offenen Bauteildaten werden anschließend in die Adapter und Konfiguration übernommen.
