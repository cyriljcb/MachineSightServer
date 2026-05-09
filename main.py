"""
main.py
Point d'entrée du simulateur MachineSight.
Lance en parallèle :
  - La boucle de simulation de l'automate
  - Le serveur OPC UA (port 4840)
  - Le serveur caméra HTTP/MJPEG (port 5000)

Usage :
    python main.py                    # caméra auto-détectée (index 0)
    python main.py --camera 1         # forcer l'index caméra
    python main.py --no-camera        # désactiver le serveur caméra
    python main.py --demo             # injecter une faute après 30s

Endpoints de debug disponibles sur port 5000 :
    POST /debug/disconnect?seconds=10   →  coupe OPC UA N secondes
    GET  /debug/status                  →  état du serveur OPC UA
"""

import asyncio
import argparse
import logging
import threading
import sys

from machine_state import MachineSimulator
from opc_server import OpcUaServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("main")


def parse_args():
    p = argparse.ArgumentParser(description="MachineSight — Simulateur d'automate industriel")
    p.add_argument("--camera",    type=int, default=0,     help="Index caméra (défaut: 0)")
    p.add_argument("--no-camera", action="store_true",     help="Désactive le serveur caméra")
    p.add_argument("--demo",      action="store_true",     help="Démarre auto + injecte une faute après 30s")
    return p.parse_args()


async def demo_scenario(sim: MachineSimulator):
    """Scénario de démo automatique pour l'entretien MachineSight."""
    log.info("Mode DEMO : démarrage automatique dans 3s...")
    await asyncio.sleep(3)
    await sim.start_machine()
    log.info("Machine démarrée. Injection d'une surtempérature dans 30s...")
    await asyncio.sleep(30)
    sim.inject_fault()
    log.info("Faute injectée ! Observez les alarmes sur l'interface C#.")
    await asyncio.sleep(20)
    await sim.reset_alarms()
    log.info("Alarmes réinitialisées.")


async def async_main(args):
    # ── Simulateur ──────────────────────────────────────────────────────────
    sim = MachineSimulator()

    def on_data_update(data):
        pass   # Le serveur OPC UA synchronise lui-même les nodes

    sim.on_update(on_data_update)

    # ── Serveur OPC UA ──────────────────────────────────────────────────────
    opc = OpcUaServer(sim)
    await opc.init()

    # ── Tâches asyncio ──────────────────────────────────────────────────────
    tasks = [
        asyncio.create_task(sim.run(),  name="simulation"),
        asyncio.create_task(opc.run(),  name="opc_ua"),
    ]

    if args.demo:
        tasks.append(asyncio.create_task(demo_scenario(sim), name="demo"))

    # ── Serveur caméra (thread séparé car Flask est synchrone) ──────────────
    if not args.no_camera:
        from camera_server import start_camera_server
        cam_thread = threading.Thread(
            target=start_camera_server,
            kwargs={"device_index": args.camera, "opc_server": opc},
            daemon=True
        )
        cam_thread.start()
        log.info(f"Serveur caméra lancé — http://0.0.0.0:5000/stream (device {args.camera})")

    log.info("=" * 60)
    log.info("  MachineSight Simulator")
    log.info("  OPC UA  → opc.tcp://<votre_ip>:4840/machinesight/simulator/")
    if not args.no_camera:
        log.info("  Caméra  → http://<votre_ip>:5000/stream")
        log.info("  Debug   → POST http://<votre_ip>:5000/debug/disconnect?seconds=10")
        log.info("            GET  http://<votre_ip>:5000/debug/status")
    log.info("  Ctrl+C pour arrêter")
    log.info("=" * 60)

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        await sim.stop()
        log.info("Simulateur arrêté proprement.")


def main():
    args = parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        log.info("Arrêt demandé.")
        sys.exit(0)


if __name__ == "__main__":
    main()