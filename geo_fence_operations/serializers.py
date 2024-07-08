import json

from rest_framework import serializers

from .models import GeoFence


class GeoFenceRequest:
    def __init__(self, type, features):
        self.type = type
        self.features = features


class GeoFencePropertiesSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="Standard Geofence")
    upper_limit = serializers.IntegerField(required=False, default=500)
    lower_limit = serializers.IntegerField(required=False, default=100)
    start_time = serializers.DateField(required=False)
    end_time = serializers.DateField(required=False)


class GeoFenceFeatureSerializer(serializers.Serializer):
    type = serializers.CharField()
    properties = GeoFencePropertiesSerializer()
    geometry = serializers.DictField(error_messages={"required": "A valid geometry object must be provided."})


class GeoFenceRequestSerializer(serializers.Serializer):
    type = serializers.CharField()
    features = serializers.ListField(child=GeoFenceFeatureSerializer(), min_length=1, max_length=1)

    def create(self, validated_data):
        return GeoFenceRequest(**validated_data)


class GeoFenceSerializer(serializers.ModelSerializer):
    altitude_ref = serializers.SerializerMethodField()
    raw_geo_fence = serializers.SerializerMethodField()
    geozone = serializers.SerializerMethodField()

    def get_raw_geo_fence(self, obj):
        raw_geo_fence = json.loads(obj.raw_geo_fence)
        return raw_geo_fence

    def get_geozone(self, obj):
        if obj.geozone:
            parsed_geo_zone = json.loads(obj.geozone)
        else:
            parsed_geo_zone = {}
        return parsed_geo_zone

    class Meta:
        model = GeoFence
        fields = "__all__"

    def get_altitude_ref(self, obj):
        return obj.get_altitude_ref_display()


class GeoSpatialMapListSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = GeoFence
        fields = (
            "id",
            "status",
            "message",
        )

    def get_status(self, obj):
        return obj.get_status_display()
