from drf_spectacular.extensions import OpenApiSerializerExtension
from rest_framework import fields
import logging

logger = logging.getLogger('__name__')

class FileFieldToBinaryExtension(OpenApiSerializerExtension):
    # Мы нацеливаемся не на конкретный сериализатор, а на сам КЛАСС ПОЛЯ из DRF
    target_class = 'rest_framework.fields.FileField' 
    
    def map_serializer_field(self, auto_schema, direction):
        # Эта логика теперь будет применяться к ЛЮБОМУ полю, которое является
        # FileField или его наследником (например, ImageField) в вашем проекте.
        return {'type': 'string', 'format': 'binary'}

#class AvatarBinaryFieldExtension(OpenApiSerializerExtension):

    #target_class = 'reader.serializers.UserProfileWriteSerializer'

    #def map_serializer_field(self, auto_schema, direction):
        #logger.info(f"AvatarBinaryFieldExtension: Checking field {self.target.field_name} for serializer {self.target_class}")
        #if self.target.field_name == 'avatar':
            #logger.info("AvatarBinaryFieldExtension: Found 'avatar' field. Applying binary format.")
           # return {'type': 'string', 'format': 'binary'}
        #return None