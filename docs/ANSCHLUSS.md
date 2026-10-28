# Anschlussplan der Software v0.1

BCM ist die GPIO-Nummer im Programm. „Pin“ bezeichnet die physische Position am 40-poligen Header. Vor Verdrahtung die Orientierung des Headers prüfen.

| Anschluss | BCM | Physischer Pin | Bedeutung |
|---|---:|---:|---|
| Koppelrelais für K1 | 17 | 11 | Aktive Heizanforderung, standardmäßig HIGH |
| Koppelrelais für K2 | 27 | 13 | Aktive Heizanforderung, standardmäßig HIGH |
| Spiegelkontakt K1 | 22 | 15 | HIGH = Öffner geschlossen, K1 abgefallen |
| Spiegelkontakt K2 | 23 | 16 | HIGH = Öffner geschlossen, K2 abgefallen |
| MAX31865 MOSI / SDI | 10 | 19 | SPI0 MOSI |
| MAX31865 MISO / SDO | 9 | 21 | SPI0 MISO |
| MAX31865 CLK | 11 | 23 | SPI0 SCLK |
| MAX31865 CS | 8 | 24 | SPI0 CE0 |
| SHT41 SDA | 2 | 3 | I²C1 SDA, Adresse standardmäßig 0x44 |
| SHT41 SCL | 3 | 5 | I²C1 SCL |
| Lichttreiber | 18 | 12 | HIGH = Licht EIN; in V1 keine Dimmung |
| Externer Watchdog | 24 | 18 | Wechselndes Lebenszeichen, ungefähr 5 Hz |
| STB-/STOP-Kreis Diagnose | 25 | 22 | HIGH = unabhängiger Sicherheitskreis bereit |
| Freigabe-/Reset-Taster | 5 | 29 | HIGH bei Tastendruck |
| Türdiagnose | 6 | 31 | HIGH = Tür geschlossen |
| 3,3 V | — | 1 oder 17 | Nur passende stromarme Sensor-/Logikversorgung |
| Masse | — | z. B. 6, 9, 14, 20, 25, 30, 34, 39 | Kleinspannungs-Logikmasse |

**Kein GPIO darf 5 V, 12 V, 24 V oder Netzspannung erhalten.** Keine Spule und keine LED-Leistung direkt an einen GPIO anschließen. Koppelrelais über geeignete 3,3-V-Treiber betreiben; Schützspulen erhalten ihre eigene, passend abgesicherte Versorgung. Falls die Relaismodule LOW-aktiv sind, muss `relay_active_high` zur realen Baugruppe passen. Ein Softwareparameter verhindert keine Einschaltimpulse beim Boot: Die Treiber müssen den AUS-Zustand ohne laufenden Pi hardwareseitig sicherstellen.

Die Eingänge haben im Programm Pull-down. Ein tatsächlich potentialfreier, ausschließlich für den Pi genutzter Kontakt kann 3,3 V zum Eingang schalten. Bei Feldspannung, gemeinsamem Sicherheitskreis oder langen Leitungen eine geeignete galvanisch getrennte Eingangsstufe einsetzen. Keine parallele Pi-Verdrahtung an spannungsführende Hilfskontaktkreise. Ein gewöhnlicher Schließer-Hilfskontakt erfüllt nicht automatisch die Anforderung an einen Öffner-Spiegelkontakt.

GPIO25 ist nur Diagnose. STB und lokaler STOP müssen die Schützspulen unabhängig vom GPIO und Prozess unterbrechen können. Der externe Watchdog muss Flanken auswerten und bei ausbleibendem Signal die Hardwarefreigabe entziehen. Sein Signal wird nur nach erfolgreichen Hauptschleifendurchläufen erzeugt, nicht durch einen unabhängig weiterlaufenden PWM-Generator. Ohne gültigen PT100, bei Fehler oder offener Tür wird das Signal nicht fortgeführt.

Der Türkontakt ist eine zusätzliche V1-Startsperre; er ist keine pauschal freigegebene Fernstarteinrichtung für einen beliebigen Ofen. Für Hardwarebetrieb sind zusätzlich die im Implementierungsplan beschriebenen unabhängigen Schutzfunktionen erforderlich.

## PT100

Der PT100 wird an den RTD-Klemmen des MAX31865 angeschlossen, nicht am GPIO. Standard ist 4-Leitertechnik mit `rtd_wires = 4` und einem beispielhaften Referenzwiderstand von `430.0` Ohm. Beides vor Betrieb anhand von Fühler, Modul und dessen Brückenstellung verifizieren. Die Modulversorgung und alle SPI-Pegel müssen zum Pi passen. MAX31865 außerhalb der Kabine montieren und SPI-Leitungen kurz halten.

## SHT41

Nur ein für die vorhandene 3,3-V-Logik geeignetes Modul verwenden. Pull-ups dürfen den Bus nicht auf 5 V ziehen. Bei langen Leitungen Buskapazität und Übertragung prüfen; gegebenenfalls geeignete Extender einsetzen. Der Sensorchip-Betriebsbereich gilt nicht automatisch für das ganze Breakout-Modul. Der Sensorheizer wird in V1 nicht eingeschaltet.

## Banklicht

GPIO18 steuert nur einen geeigneten Logikeingang eines LED-Treibers/MOSFET-Moduls. LED-Leistung über ein dafür ausgelegtes, abgesichertes 12-/24-V-System führen. Erst Spannung und Netzteil-/LED-Art bestätigen. V1 schaltet EIN/AUS. Im Überwachungsmodus sind sämtliche GPIO-Ausgänge, einschließlich Licht, unbenutzt.
