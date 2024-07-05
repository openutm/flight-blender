import json
from typing import List

from rest_framework import serializers

from common.data_definitions import OPERATION_STATES, OPERATOR_EVENT_LOOKUP
from common.database_operations import ArgonServerDatabaseReader
from conformance_monitoring_operations.conformance_checks_handler import (
    FlightOperationConformanceHelper,
)
from scd_operations.dss_scd_helper import OperationalIntentReferenceHelper
from scd_operations.scd_data_definitions import Volume4D

from .models import FlightDeclaration
from .utils import OperationalIntentsConverter


class FlightDeclarationSerializer(serializers.ModelSerializer):
    operational_intent = serializers.SerializerMethodField()
    flight_declaration_geojson = serializers.SerializerMethodField()
    flight_declaration_raw_geojson = serializers.SerializerMethodField()

    def get_flight_declaration_geojson(self, obj):
        o = json.loads(obj.operational_intent)
        volumes = o["volumes"]
        volumes_list: List[Volume4D] = []
        my_operational_intent_parser = OperationalIntentReferenceHelper()
        for v in volumes:
            parsed_volume = my_operational_intent_parser.parse_volume_to_volume4D(v)
            volumes_list.append(parsed_volume)
        my_operational_intent_converter = OperationalIntentsConverter()
        my_operational_intent_converter.convert_operational_intent_to_geo_json(volumes=volumes_list)
        return my_operational_intent_converter.geo_json

    def get_flight_declaration_raw_geojson(self, obj):
        return json.loads(obj.flight_declaration_raw_geojson)

    def get_operational_intent(self, obj):
        return json.loads(obj.operational_intent)

    class Meta:
        model = FlightDeclaration
        fields = (
            "operational_intent",
            "originating_party",
            "type_of_operation",
            "id",
            "state",
            "is_approved",
            "start_datetime",
            "end_datetime",
            "flight_declaration_geojson",
            "flight_declaration_raw_geojson",
            "approved_by",
            "submitted_by",
        )


class FlightDeclarationApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlightDeclaration
        fields = (
            "is_approved",
            "approved_by",
        )


class FlightDeclarationStateSerializer(serializers.ModelSerializer):
    def validate_state(self, value):
        if self.instance and value not in list(OPERATOR_EVENT_LOOKUP.keys()):
            raise serializers.ValidationError("An operator can only set the state to Activated (2), Contingent (4) or Ended (5) using this end point")

        current_state = self.instance.state
        event = OPERATOR_EVENT_LOOKUP[value]

        if current_state in [5, 6, 7, 8]:
            raise serializers.ValidationError("Cannot change state of an operation that has already set as ended, withdrawn, cancelled or rejected")

        my_conformance_helper = FlightOperationConformanceHelper(str(self.instance.id))
        transition_valid = my_conformance_helper.verify_operation_state_transition(original_state=current_state, new_state=value, event=event)

        if not transition_valid:
            raise serializers.ValidationError(
                "State transition to {new_state} from current state of {current_state} is not allowed per the ASTM standards".format(
                    new_state=OPERATION_STATES[value][1],
                    current_state=OPERATION_STATES[current_state][1],
                )
            )

        return value

    def update(self, instance, validated_data):
        my_database_reader = ArgonServerDatabaseReader()
        fd = my_database_reader.get_flight_declaration_by_id(instance.id)
        original_state = fd.state
        FlightDeclaration.objects.filter(pk=instance.id).update(**validated_data)

        # Save the database and trigger management command
        new_state = validated_data["state"]
        event = OPERATOR_EVENT_LOOKUP[new_state]
        fd.add_state_history_entry(
            original_state=original_state,
            new_state=new_state,
            notes="State changed by operator",
        )
        my_conformance_helper = FlightOperationConformanceHelper(flight_declaration_id=str(instance.id))
        my_conformance_helper.manage_operation_state_transition(original_state=original_state, new_state=new_state, event=event)
        return fd

    class Meta:
        model = FlightDeclaration
        fields = (
            "state",
            "submitted_by",
        )
