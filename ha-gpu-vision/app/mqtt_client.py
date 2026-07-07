"""MQTT-Anbindung an Home Assistant inkl. Auto-Discovery der Sensoren."""
import json
import logging
import socket

import paho.mqtt.client as mqtt

import config

log = logging.getLogger("mqtt")

AVAILABILITY_TOPIC = f"{config.MQTT_BASE_TOPIC}/status"
RESULT_TOPIC = f"{config.MQTT_BASE_TOPIC}/result"
EVENT_TOPIC = f"{config.MQTT_BASE_TOPIC}/event"


class HomeAssistantMqtt:
    """Publiziert Analyseergebnisse und legt die Sensoren per MQTT-Discovery an."""

    def __init__(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=config.DEVICE_ID
        )
        if config.MQTT_USER:
            self.client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
        self.client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.connected = False

    def start(self):
        try:
            self.client.connect_async(config.MQTT_HOST, config.MQTT_PORT)
            self.client.loop_start()
        except (socket.gaierror, OSError) as exc:
            log.error("MQTT-Verbindung fehlgeschlagen: %s", exc)

    def stop(self):
        if self.connected:
            self.client.publish(AVAILABILITY_TOPIC, "offline", retain=True)
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.error("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        self.connected = True
        log.info("Mit MQTT-Broker %s:%s verbunden.", config.MQTT_HOST, config.MQTT_PORT)
        client.publish(AVAILABILITY_TOPIC, "online", retain=True)
        self._publish_discovery()

    # ---------------------------------------------------------------- Discovery

    def _publish_discovery(self):
        device = {
            "identifiers": [config.DEVICE_ID],
            "name": config.DEVICE_NAME,
            "manufacturer": "ha-gpu-vision",
            "model": "YOLO + fast-alpr",
        }
        sensors = {
            "last_event": {
                "name": "Letztes Ereignis",
                "icon": "mdi:cctv",
                "value_template": "{{ value_json.description }}",
                "json_attributes_topic": RESULT_TOPIC,
            },
            "last_plate": {
                "name": "Letztes Kennzeichen",
                "icon": "mdi:car-search",
                "value_template": (
                    "{{ value_json.plates[0].text if value_json.plates else 'unbekannt' }}"
                ),
            },
            "persons": {
                "name": "Personen",
                "icon": "mdi:walk",
                "value_template": "{{ value_json.persons }}",
                "state_class": "measurement",
            },
            "vehicles": {
                "name": "Fahrzeuge",
                "icon": "mdi:car",
                "value_template": "{{ value_json.vehicles }}",
                "state_class": "measurement",
            },
        }
        for key, extra in sensors.items():
            payload = {
                "unique_id": f"{config.DEVICE_ID}_{key}",
                "state_topic": RESULT_TOPIC,
                "availability_topic": AVAILABILITY_TOPIC,
                "device": device,
                **extra,
            }
            topic = (
                f"{config.HA_DISCOVERY_PREFIX}/sensor/{config.DEVICE_ID}/{key}/config"
            )
            self.client.publish(topic, json.dumps(payload), retain=True)
        log.info("MQTT-Discovery für %d Sensoren veröffentlicht.", len(sensors))

    # ---------------------------------------------------------------- Publish

    def publish_result(self, result_dict: dict):
        payload = json.dumps(result_dict, ensure_ascii=False)
        # retained: Sensoren behalten den letzten Zustand nach HA-Neustart
        self.client.publish(RESULT_TOPIC, payload, retain=True)
        # nicht retained: für event-basierte Automationen (jede Analyse triggert)
        self.client.publish(EVENT_TOPIC, payload)
        log.info("Ergebnis publiziert: %s", result_dict.get("description"))
