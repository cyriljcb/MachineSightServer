"""
opc_server.py
Expose les données de la machine via OPC UA (port 4840).
Chaque capteur = un Node OPC UA que le client C# pourra lire/écrire.

NodeIds fixes (namespace 2) — ne changent pas après reconnexion :
    Sensors
        1002  Temperature_C
        1003  Pressure_Bar
        1004  Speed_RPM
        1005  Vibration_mms
        1006  Current_A
        1007  ProductionCount
        1008  CycleTime_ms
    Status
        1011  StatusCode
        1012  StatusLabel
    Alarms
        1021  AlarmTemperature
        1022  AlarmPressure
        1023  AlarmVibration
        1024  AlarmEmergency
    Commands  (writable)
        1031  CmdStart
        1032  CmdStop
        1033  CmdEmergency
        1034  CmdResetAlarms
        1035  CmdInjectFault
"""

import asyncio
import logging
from asyncua import Server, ua
from machine_state import MachineSimulator, MachineData

log = logging.getLogger("opc_server")

ENDPOINT  = "opc.tcp://0.0.0.0:4840/machinesight/simulator/"
NAMESPACE = "http://machinesight.local/simulator"


class OpcUaServer:

    def __init__(self, simulator: MachineSimulator):
        self._sim    = simulator
        self._server = Server()
        self._nodes: dict = {}

        # ── Déconnexion temporaire ──────────────────────────────────────────
        self._disconnect_event    = asyncio.Event()
        self._disconnect_duration: float = 0.0

    # ── API publique ────────────────────────────────────────────────────────

    def schedule_disconnect(self, seconds: float) -> bool:
        """
        Demande une déconnexion temporaire du serveur OPC UA.
        Appelable depuis n'importe quel thread (Flask).
        Retourne False si une déconnexion est déjà en cours.
        """
        if self._disconnect_event.is_set():
            return False
        self._disconnect_duration = max(1.0, float(seconds))
        self._disconnect_event.set()
        return True

    @property
    def is_disconnected(self) -> bool:
        return self._disconnect_event.is_set()

    # ── Initialisation ──────────────────────────────────────────────────────

    async def init(self):
        await self._server.init()
        self._server.set_endpoint(ENDPOINT)
        self._server.set_server_name("MachineSight Simulator")
        self._server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        idx = await self._server.register_namespace(NAMESPACE)
        await self._build_nodes(idx)
        log.info(f"OPC UA server initialisé — {ENDPOINT}")

    # ── Construction des nodes (NodeIds fixes) ──────────────────────────────

    async def _build_nodes(self, idx: int):
        """
        Crée l'arborescence OPC UA avec des NodeIds numériques fixes.
        Ainsi, après une reconnexion, le client C# retrouve exactement
        les mêmes identifiants sans avoir besoin de re-browsinger.
        """
        objects = self._server.nodes.objects

        # ── Machine (racine) ────────────────────────────────────────────────
        machine = await objects.add_object(ua.NodeId(1000, idx), "Machine")

        # ── helpers ─────────────────────────────────────────────────────────
        async def var(parent, node_id_int: int, name: str, init_val):
            node = await parent.add_variable(
                ua.NodeId(node_id_int, idx), name, init_val)
            await node.set_writable(False)
            return node

        async def cmd(node_id_int: int, name: str):
            node = await commands.add_variable(
                ua.NodeId(node_id_int, idx), name, False)
            await node.set_writable(True)
            return node

        # ── Sensors ─────────────────────────────────────────────────────────
        sensors = await machine.add_object(ua.NodeId(1001, idx), "Sensors")
        self._nodes["temperature"]      = await var(sensors, 1002, "Temperature_C",  22.0)
        self._nodes["pressure"]         = await var(sensors, 1003, "Pressure_Bar",    1.013)
        self._nodes["speed_rpm"]        = await var(sensors, 1004, "Speed_RPM",       0.0)
        self._nodes["vibration"]        = await var(sensors, 1005, "Vibration_mms",   0.0)
        self._nodes["current_a"]        = await var(sensors, 1006, "Current_A",       0.0)
        self._nodes["production_count"] = await var(sensors, 1007, "ProductionCount", 0)
        self._nodes["cycle_time_ms"]    = await var(sensors, 1008, "CycleTime_ms",    0.0)

        # ── Status ──────────────────────────────────────────────────────────
        status_obj = await machine.add_object(ua.NodeId(1010, idx), "Status")
        self._nodes["status"]       = await var(status_obj, 1011, "StatusCode",  0)
        self._nodes["status_label"] = await var(status_obj, 1012, "StatusLabel", "Arrêtée")

        # ── Alarms ──────────────────────────────────────────────────────────
        alarms = await machine.add_object(ua.NodeId(1020, idx), "Alarms")
        self._nodes["alarm_temp"]      = await var(alarms, 1021, "AlarmTemperature", False)
        self._nodes["alarm_pressure"]  = await var(alarms, 1022, "AlarmPressure",    False)
        self._nodes["alarm_vibration"] = await var(alarms, 1023, "AlarmVibration",   False)
        self._nodes["alarm_emergency"] = await var(alarms, 1024, "AlarmEmergency",   False)

        # ── Commands (writable) ─────────────────────────────────────────────
        commands = await machine.add_object(ua.NodeId(1030, idx), "Commands")
        self._nodes["cmd_start"]     = await cmd(1031, "CmdStart")
        self._nodes["cmd_stop"]      = await cmd(1032, "CmdStop")
        self._nodes["cmd_emergency"] = await cmd(1033, "CmdEmergency")
        self._nodes["cmd_reset"]     = await cmd(1034, "CmdResetAlarms")
        self._nodes["cmd_fault"]     = await cmd(1035, "CmdInjectFault")

        log.info("Nodes OPC UA créés avec NodeIds fixes (ns=2, 1002–1035).")

    # ── Boucle principale ───────────────────────────────────────────────────

    async def run(self):
        """
        Boucle principale. Gère le cycle normal et les déconnexions temporaires.

        Pendant une déconnexion :
          - Le serveur OPC UA est arrêté → les clients reçoivent BadConnectionClosed.
          - Après `_disconnect_duration` secondes, le serveur redémarre
            automatiquement sur le même endpoint avec les mêmes NodeIds.
        """
        while True:
            async with self._server:
                log.info("OPC UA server démarré sur port 4840")
                await self._normal_loop()

            # ── Phase de déconnexion ────────────────────────────────────────
            duration = self._disconnect_duration
            log.warning(
                f"[DEBUG] OPC UA server DÉCONNECTÉ pendant {duration:.0f}s "
                f"— les clients vont perdre la connexion.")
            await asyncio.sleep(duration)
            self._disconnect_event.clear()

            # Laisser l'OS libérer le port avant de rebinder
            await asyncio.sleep(2.0)
            log.info("[DEBUG] OPC UA server RECONNECTÉ — redémarrage.")

            # Ré-instancier le serveur asyncua et recréer les nodes
            self._server = Server()
            await self._reinit_server()

    async def _normal_loop(self):
        """Tourne jusqu'à ce qu'une déconnexion soit demandée."""
        while not self._disconnect_event.is_set():
            await self._sync_nodes()
            await self._handle_commands()
            await asyncio.sleep(0.1)

    # ── Ré-initialisation après reconnexion ─────────────────────────────────

    async def _reinit_server(self):
        """Recrée le serveur et les nodes OPC UA après un redémarrage."""
        await self._server.init()
        self._server.set_endpoint(ENDPOINT)
        self._server.set_server_name("MachineSight Simulator")
        self._server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        idx = await self._server.register_namespace(NAMESPACE)
        await self._build_nodes(idx)
        log.info("OPC UA server ré-initialisé après reconnexion.")

    # ── Synchronisation des valeurs ─────────────────────────────────────────

    async def _sync_nodes(self):
        d: MachineData = self._sim.data

        async def wf(key, val):
            await self._nodes[key].write_value(
                ua.DataValue(ua.Variant(float(val), ua.VariantType.Double)))

        async def wi(key, val):
            await self._nodes[key].write_value(
                ua.DataValue(ua.Variant(int(val), ua.VariantType.Int64)))

        async def wb(key, val):
            await self._nodes[key].write_value(
                ua.DataValue(ua.Variant(bool(val), ua.VariantType.Boolean)))

        await wf("temperature",      d.temperature)
        await wf("pressure",         d.pressure)
        await wf("speed_rpm",        d.speed_rpm)
        await wf("vibration",        d.vibration)
        await wf("current_a",        d.current_a)
        await wi("production_count", d.production_count)
        await wf("cycle_time_ms",    d.cycle_time_ms)
        await wi("status",           d.status)
        await self._nodes["status_label"].write_value(d.status_label)
        await wb("alarm_temp",       d.alarm_temp)
        await wb("alarm_pressure",   d.alarm_pressure)
        await wb("alarm_vibration",  d.alarm_vibration)
        await wb("alarm_emergency",  d.alarm_emergency)

    # ── Lecture des commandes ────────────────────────────────────────────────

    async def _handle_commands(self):
        async def read(key):
            return await self._nodes[key].read_value()

        async def clear(key):
            await self._nodes[key].write_value(False)

        if await read("cmd_start"):
            await self._sim.start_machine()
            await clear("cmd_start")

        if await read("cmd_stop"):
            await self._sim.stop_machine()
            await clear("cmd_stop")

        if await read("cmd_emergency"):
            await self._sim.emergency_stop()
            await clear("cmd_emergency")

        if await read("cmd_reset"):
            await self._sim.reset_alarms()
            await clear("cmd_reset")

        if await read("cmd_fault"):
            self._sim.inject_fault()
            await clear("cmd_fault")