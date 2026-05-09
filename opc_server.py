"""
opc_server.py
Expose les données de la machine via OPC UA (port 4840).
Chaque capteur = un Node OPC UA que le client C# pourra lire/écrire.
"""

import asyncio
import logging
from asyncua import Server, ua
from machine_state import MachineSimulator, MachineData

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("opc_server")

ENDPOINT = "opc.tcp://0.0.0.0:4840/machinesight/simulator/"
NAMESPACE = "http://machinesight.local/simulator"


class OpcUaServer:

    def __init__(self, simulator: MachineSimulator):
        self._sim = simulator
        self._server = Server()
        self._nodes: dict = {}

    async def init(self):
        await self._server.init()
        self._server.set_endpoint(ENDPOINT)
        self._server.set_server_name("MachineSight Simulator")

        self._server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        idx = await self._server.register_namespace(NAMESPACE)

        objects = self._server.nodes.objects
        machine = await objects.add_object(idx, "Machine")

        async def var(parent, name, init_val):
            node = await parent.add_variable(idx, name, init_val)
            await node.set_writable(False)
            return node

        sensors = await machine.add_object(idx, "Sensors")
        self._nodes["temperature"]      = await var(sensors, "Temperature_C",  22.0)
        self._nodes["pressure"]         = await var(sensors, "Pressure_Bar",    1.013)
        self._nodes["speed_rpm"]        = await var(sensors, "Speed_RPM",       0.0)
        self._nodes["vibration"]        = await var(sensors, "Vibration_mms",   0.0)
        self._nodes["current_a"]        = await var(sensors, "Current_A",       0.0)
        self._nodes["production_count"] = await var(sensors, "ProductionCount", 0)
        self._nodes["cycle_time_ms"]    = await var(sensors, "CycleTime_ms",    0.0)

        status_obj = await machine.add_object(idx, "Status")
        self._nodes["status"]       = await var(status_obj, "StatusCode",  0)
        self._nodes["status_label"] = await var(status_obj, "StatusLabel", "Arrêtée")

        alarms = await machine.add_object(idx, "Alarms")
        self._nodes["alarm_temp"]      = await var(alarms, "AlarmTemperature", False)
        self._nodes["alarm_pressure"]  = await var(alarms, "AlarmPressure",    False)
        self._nodes["alarm_vibration"] = await var(alarms, "AlarmVibration",   False)
        self._nodes["alarm_emergency"] = await var(alarms, "AlarmEmergency",   False)

        commands = await machine.add_object(idx, "Commands")

        async def cmd(name, init=False):
            node = await commands.add_variable(idx, name, init)
            await node.set_writable(True)
            return node

        self._nodes["cmd_start"]     = await cmd("CmdStart")
        self._nodes["cmd_stop"]      = await cmd("CmdStop")
        self._nodes["cmd_emergency"] = await cmd("CmdEmergency")
        self._nodes["cmd_reset"]     = await cmd("CmdResetAlarms")
        self._nodes["cmd_fault"]     = await cmd("CmdInjectFault")

        log.info(f"OPC UA server initialisé — {ENDPOINT}")

    async def run(self):
        async with self._server:
            log.info("OPC UA server démarré sur port 4840")
            while True:
                await self._sync_nodes()
                await self._handle_commands()
                await asyncio.sleep(0.1)

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