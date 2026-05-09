# MachineSight — Simulateur d'automate industriel
## Installation sur Raspberry Pi

### 1. Prérequis

```bash
sudo apt update
sudo apt install python3-pip python3-opencv -y
pip3 install asyncua flask --break-system-packages
```

### 2. Copier les fichiers

Copie les 4 fichiers dans un dossier sur le Raspberry :
```
~/machinesight/
  ├── main.py
  ├── machine_state.py
  ├── opc_server.py
  └── camera_server.py
```

### 3. Trouver l'IP du Raspberry

```bash
hostname -I
# ex : 192.168.1.42
```

### 4. Lancer le simulateur

```bash
cd ~/machinesight

# Mode normal (caméra Logitech auto-détectée)
python3 main.py

# Si la caméra est sur un autre index
python3 main.py --camera 1

# Sans caméra (OPC UA seulement)
python3 main.py --no-camera

# Mode démo (démarrage auto + injection de faute après 30s)
python3 main.py --demo
```

### 5. Vérifier que ça fonctionne

Depuis ton PC Windows :

```
# Flux caméra (coller dans Chrome)
http://192.168.1.42:5000/stream

# Statut caméra
http://192.168.1.42:5000/status

# OPC UA → à configurer dans l'interface C# Avalonia :
opc.tcp://192.168.1.42:4840/machinesight/simulator/
```

---

## Nodes OPC UA disponibles

### Capteurs (lecture seule)
| Node | Type | Unité | Description |
|---|---|---|---|
| `Sensors/Temperature_C` | Double | °C | Température moteur (normale : 60–80°C) |
| `Sensors/Pressure_Bar` | Double | bar | Pression hydraulique (normale : 2.5–4.0 bar) |
| `Sensors/Speed_RPM` | Double | tr/min | Vitesse de rotation (nominal : 1500 tr/min) |
| `Sensors/Vibration_mms` | Double | mm/s | Niveau de vibration (alarme > 8 mm/s) |
| `Sensors/Current_A` | Double | A | Courant moteur |
| `Sensors/ProductionCount` | Int | pcs | Compteur de pièces produites |
| `Sensors/CycleTime_ms` | Double | ms | Durée du dernier cycle |

### État machine (lecture seule)
| Node | Type | Description |
|---|---|---|
| `Status/StatusCode` | Int | 0=Arrêtée 1=Démarrage 2=Marche 3=Warning 4=Alarme 5=Urgence |
| `Status/StatusLabel` | String | Libellé lisible du statut |

### Alarmes (lecture seule)
| Node | Type | Description |
|---|---|---|
| `Alarms/AlarmTemperature` | Bool | Température > 85°C |
| `Alarms/AlarmPressure` | Bool | Pression hors plage |
| `Alarms/AlarmVibration` | Bool | Vibration > 6 mm/s |
| `Alarms/AlarmEmergency` | Bool | Arrêt d'urgence actif |

### Commandes (lecture/écriture)
| Node | Type | Description |
|---|---|---|
| `Commands/CmdStart` | Bool | Écrire `true` pour démarrer la machine |
| `Commands/CmdStop` | Bool | Écrire `true` pour arrêter normalement |
| `Commands/CmdEmergency` | Bool | Écrire `true` pour arrêt d'urgence |
| `Commands/CmdResetAlarms` | Bool | Écrire `true` pour réinitialiser les alarmes |
| `Commands/CmdInjectFault` | Bool | Écrire `true` pour simuler une surtempérature |

---

## Architecture du code

```
main.py              Point d'entrée, orchestre les 3 composants
machine_state.py     Simulation physique de l'automate (capteurs, alarmes, cycles)
opc_server.py        Serveur OPC UA — expose les données et reçoit les commandes
camera_server.py     Capture webcam + streaming MJPEG HTTP
```

