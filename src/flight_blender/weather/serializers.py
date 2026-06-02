from rest_framework import serializers


class HourlySerializer(serializers.Serializer):
    time = serializers.ListField(child=serializers.DateTimeField())
    temperature_2m = serializers.ListField(child=serializers.FloatField())
    showers = serializers.ListField(child=serializers.FloatField())
    windspeed_10m = serializers.ListField(child=serializers.FloatField())
    winddirection_10m = serializers.ListField(child=serializers.IntegerField())
    windgusts_10m = serializers.ListField(child=serializers.FloatField())


class HourlyUnitsSerializer(serializers.Serializer):
    time = serializers.CharField()
    temperature_2m = serializers.CharField()
    showers = serializers.CharField()
    windspeed_10m = serializers.CharField()
    winddirection_10m = serializers.CharField()
    windgusts_10m = serializers.CharField()


class WeatherSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    generationtime_ms = serializers.FloatField()
    utc_offset_seconds = serializers.IntegerField()
    timezone = serializers.CharField()
    timezone_abbreviation = serializers.CharField()
    elevation = serializers.FloatField()
    hourly_units = HourlyUnitsSerializer()
    hourly = HourlySerializer()
