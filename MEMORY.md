## GOTCHA

- Symptom: macOS BLE commands launched through SSH fail or hang with CoreBluetooth authorization `notDetermined`. Cause: Bluetooth TCC approval is tied to a GUI-launched responsible process. Fix: launch the BLE command from local Terminal or a signed local app and approve Bluetooth access before retrying.
- Symptom: an iOS foreground camera attempt can remain busy even when stage timeout code exists. Cause: session preparation cancels an attempt started too early, or failure handling switches to unbounded background reconnect solely because Background is configured. Fix: begin the foreground timeout after transient cleanup and preserve attempt origin when choosing bounded failure versus background retry.

## TASTE
