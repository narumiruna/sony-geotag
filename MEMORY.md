## GOTCHA

- Symptom: macOS BLE commands launched through SSH fail or hang with CoreBluetooth authorization `notDetermined`. Cause: Bluetooth TCC approval is tied to a GUI-launched responsible process. Fix: launch the BLE command from local Terminal or a signed local app and approve Bluetooth access before retrying.

## TASTE
