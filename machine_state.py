"""
machine_state.py
Simule l'état interne d'un automate industriel.
Génère des données réalistes avec dérives, alarmes et cycles machine.
"""

import asyncio
import random
import math
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable


class MachineStatus(IntEnum):
    STOPPED   = 0
    STARTING  = 1
    RUNNING   = 2
    WARNING   = 3
    ALARM     = 4
    EMERGENCY = 5


STATUS_LABELS = {
    MachineStatus.STOPPED:   "Arrêtée",
    MachineStatus.STARTING:  "Démarrage",
    MachineStatus.RUNNING:   "En marche",
    MachineStatus.WARNING:   "Avertissement",
    MachineStatus.ALARM:     "Alarme",
    MachineStatus.EMERGENCY: "Arrêt d'urgence",
}


@dataclass
class MachineData:
    # Identification
    machine_id: str = "MACHINE_001"
    status: int = MachineStatus.STOPPED
    status_label: str = "Arrêtée"

    # Capteurs physiques simulés
    temperature: float = 22.0       # °C  — normale: 60–80
    pressure: float = 1.013         # bar — normale: 2.5–4.0
    speed_rpm: float = 0.0          # tr/min — normale: 1200–1800
    vibration: float = 0.0          # mm/s — alarme > 8.0
    current_a: float = 0.0          # Ampères moteur
    production_count: int = 0       # pièces produites
    cycle_time_ms: float = 0.0      # durée du dernier cycle

    # Alarmes actives
    alarm_temp: bool = False
    alarm_pressure: bool = False
    alarm_vibration: bool = False
    alarm_emergency: bool = False

    # Timestamp
    timestamp: float = field(default_factory=time.time)


class MachineSimulator:
    """
    Simule le comportement d'un automate industriel.
    Le cycle de vie : STOPPED → STARTING → RUNNING → (WARNING?) → STOPPED
    """

    # Seuils d'alarme
    TEMP_WARNING  = 85.0
    TEMP_ALARM    = 95.0
    PRESS_LOW     = 2.0
    PRESS_HIGH    = 4.5
    VIB_WARNING   = 6.0
    VIB_ALARM     = 10.0
    RPM_NOMINAL   = 1500.0

    def __init__(self):
        self.data = MachineData()
        self._running = False
        self._task: asyncio.Task | None = None
        self._t = 0.0                    # temps simulé (pour les sinusoïdes)
        self._startup_progress = 0.0     # 0..1 pendant le démarrage
        self._inject_fault = False       # déclenchable depuis l'UI
        self._on_update: Callable | None = None

    # ── API publique ────────────────────────────────────────────────────────

    def on_update(self, callback: Callable):
        """Enregistre un callback appelé à chaque mise à jour de l'état."""
        self._on_update = callback

    async def start_machine(self):
        if self.data.status in (MachineStatus.RUNNING, MachineStatus.STARTING):
            return
        self._set_status(MachineStatus.STARTING)
        self._startup_progress = 0.0

    async def stop_machine(self):
        self._set_status(MachineStatus.STOPPED)
        self._startup_progress = 0.0

    async def emergency_stop(self):
        self._set_status(MachineStatus.EMERGENCY)
        self.data.alarm_emergency = True

    async def reset_alarms(self):
        if self.data.status == MachineStatus.EMERGENCY:
            self._set_status(MachineStatus.STOPPED)
        self.data.alarm_emergency = False
        self.data.alarm_temp = False
        self.data.alarm_pressure = False
        self.data.alarm_vibration = False
        self._inject_fault = False

    def inject_fault(self):
        """Injecte une surtempérature pour tester les alarmes."""
        self._inject_fault = True

    # ── Boucle de simulation ────────────────────────────────────────────────

    async def run(self):
        """Boucle principale à appeler en arrière-plan (asyncio.create_task)."""
        self._running = True
        while self._running:
            self._t += 0.1
            await self._tick()
            if self._on_update:
                self._on_update(self.data)
            await asyncio.sleep(0.1)   # mise à jour 10 Hz

    async def stop(self):
        self._running = False

    # ── Tick interne ────────────────────────────────────────────────────────

    async def _tick(self):
        d = self.data
        d.timestamp = time.time()
        status = MachineStatus(d.status)

        if status == MachineStatus.STOPPED:
            self._decay_to_idle()

        elif status == MachineStatus.STARTING:
            self._startup_progress = min(1.0, self._startup_progress + 0.01)
            self._ramp_up(self._startup_progress)
            if self._startup_progress >= 1.0:
                self._set_status(MachineStatus.RUNNING)

        elif status in (MachineStatus.RUNNING, MachineStatus.WARNING):
            self._simulate_running()
            self._check_alarms()

        elif status == MachineStatus.ALARM:
            # En alarme : la machine ralentit mais ne s'arrête pas seule
            self._simulate_running(degraded=True)
            self._check_alarms()

        elif status == MachineStatus.EMERGENCY:
            self._decay_to_idle(fast=True)

        d.status_label = STATUS_LABELS[MachineStatus(d.status)]

    def _simulate_running(self, degraded: bool = False):
        d = self.data
        t = self._t

        # Vitesse : sinusoïde lente autour du nominal ± 50 tr/min
        rpm_target = self.RPM_NOMINAL if not degraded else self.RPM_NOMINAL * 0.6
        d.speed_rpm = rpm_target + 50 * math.sin(t * 0.3) + random.gauss(0, 5)

        # Température : monte progressivement puis stabilise, + bruit
        temp_target = 72.0 if not degraded else 90.0
        if self._inject_fault:
            temp_target = 102.0   # surtempérature injectée
        d.temperature += (temp_target - d.temperature) * 0.02 + random.gauss(0, 0.3)

        # Pression : suit une onde lente + bruit
        d.pressure = 3.2 + 0.4 * math.sin(t * 0.15) + random.gauss(0, 0.05)

        # Vibration : bruit de fond + pics aléatoires
        vib_base = 2.5 if not degraded else 7.5
        spike = random.gauss(0, 0.8)
        if random.random() < 0.02:    # pic rare
            spike = random.uniform(3, 6)
        d.vibration = max(0, vib_base + spike)

        # Courant moteur : proportionnel à la vitesse
        d.current_a = (d.speed_rpm / self.RPM_NOMINAL) * 18.0 + random.gauss(0, 0.2)

        # Compteur de production : une pièce toutes ~2 s en nominal
        if random.random() < 0.05 and not degraded:
            d.production_count += 1
            d.cycle_time_ms = random.gauss(2000, 80)

    def _ramp_up(self, progress: float):
        """Monte progressivement les capteurs pendant le démarrage."""
        d = self.data
        d.speed_rpm  = self.RPM_NOMINAL * progress + random.gauss(0, 10)
        d.temperature = 22.0 + 50.0 * progress + random.gauss(0, 0.5)
        d.pressure    = 1.013 + 2.2 * progress  + random.gauss(0, 0.05)
        d.vibration   = 2.0 * progress           + random.gauss(0, 0.3)
        d.current_a   = 18.0 * progress          + random.gauss(0, 0.2)

    def _decay_to_idle(self, fast: bool = False):
        """Ramène les capteurs vers les valeurs ambiantes."""
        d = self.data
        rate = 0.05 if fast else 0.02
        d.speed_rpm   = max(0, d.speed_rpm   - d.speed_rpm   * rate * 3)
        d.temperature = d.temperature + (22.0 - d.temperature) * rate
        d.pressure    = d.pressure    + (1.013 - d.pressure)  * rate
        d.vibration   = max(0, d.vibration - d.vibration * rate * 2)
        d.current_a   = max(0, d.current_a  - d.current_a  * rate * 3)

    def _check_alarms(self):
        d = self.data

        # Température
        d.alarm_temp = d.temperature > self.TEMP_WARNING
        # Pression hors plage
        d.alarm_pressure = (d.pressure < self.PRESS_LOW or d.pressure > self.PRESS_HIGH)
        # Vibration
        d.alarm_vibration = d.vibration > self.VIB_WARNING

        any_alarm = d.alarm_temp or d.alarm_pressure or d.alarm_vibration

        if d.temperature > self.TEMP_ALARM or d.vibration > self.VIB_ALARM:
            self._set_status(MachineStatus.ALARM)
        elif any_alarm:
            self._set_status(MachineStatus.WARNING)
        else:
            self._set_status(MachineStatus.RUNNING)

    def _set_status(self, status: MachineStatus):
        self.data.status = int(status)
        self.data.status_label = STATUS_LABELS[status]