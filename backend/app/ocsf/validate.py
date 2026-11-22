"""Check an event against the vendored OCSF schema: class, required attributes, unknown attributes, types, enum values, nested objects,
profile-only attributes, and the type_uid rule. Returns a list of plain problems (empty = valid).

Not checked (the schema expresses them as constraints we do not vendor): "at least one of" rules inside objects and regular expressions.
"""
from __future__ import annotations

from . import schema

_INT = {"integer_t", "long_t", "timestamp_t", "port_t"}
_STR = {"string_t", "hostname_t", "ip_t", "url_t", "username_t", "uuid_t", "email_t", "file_name_t", "file_path_t", "file_hash_t", "mac_t", "subnet_t",
        "process_name_t", "resource_uid_t", "reg_key_path_t", "datetime_t", "bytestring_t"}


def _type_ok(kind: str, value) -> bool:
    if kind in _INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind in _STR:
        return isinstance(value, str)
    if kind == "float_t":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean_t":
        return isinstance(value, bool)
    if kind in ("json_t", "object_t"):
        return True
    return False


def _check_attr(path: str, name: str, spec: dict, value, objects: dict, errors: list[str], profiles: set[str]) -> None:
    where = f"{path}{name}"
    items = value if spec.get("is_array") else [value]
    if spec.get("is_array") and not isinstance(value, list):
        errors.append(f"{where}: must be a list")
        return
    for i, v in enumerate(items):
        at = f"{where}[{i}]" if spec.get("is_array") else where
        if spec["type"] == "object_t" and spec.get("object_type") and spec["object_type"] != "object":       # "object" is the free-form container (unmapped, ...)
            if not isinstance(v, dict):
                errors.append(f"{at}: must be an object")
            else:
                _check_obj(at + ".", objects[spec["object_type"]], v, objects, errors, profiles)
        elif not _type_ok(spec["type"], v):
            errors.append(f"{at}: expected {spec['type']}, got {type(v).__name__}")
        elif spec.get("enum") and spec["type"] in _INT and str(v) not in spec["enum"]:
            errors.append(f"{at}: {v} is not one of {sorted(spec['enum'], key=int)}")


def _check_obj(path: str, definition: dict, value: dict, objects: dict, errors: list[str], profiles: set[str]) -> None:
    attrs = definition["attributes"]
    for name, v in value.items():
        spec = attrs.get(name)
        if spec is None:
            errors.append(f"{path}{name}: not an attribute of {definition.get('name') or definition.get('caption')}")
            continue
        if spec.get("profiles") and not (set(spec["profiles"]) & profiles):
            errors.append(f"{path}{name}: needs the {'/'.join(spec['profiles'])} profile in metadata.profiles")
            continue
        _check_attr(path, name, spec, v, objects, errors, profiles)
    for name, spec in attrs.items():
        if spec["requirement"] == "required" and name not in value and not (spec.get("profiles") and not (set(spec["profiles"]) & profiles)):
            errors.append(f"{path}{name}: required")


def validate(event: dict) -> list[str]:
    s = schema.load()
    errors: list[str] = []
    cls = next((c for c in s["classes"].values() if c["uid"] == event.get("class_uid")), None)
    if cls is None:
        return [f"class_uid {event.get('class_uid')!r} is not one of the classes Agent Studio emits"]
    meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    profiles = set(meta.get("profiles") or [])
    for p in profiles:
        if p not in s["profiles"]:
            errors.append(f"metadata.profiles: unknown profile '{p}'")
        elif p not in (cls.get("profiles") or []):
            errors.append(f"metadata.profiles: the {cls['name']} class does not support the '{p}' profile")
    _check_obj("", cls, event, s["objects"], errors, profiles)
    if event.get("category_uid") != cls["category_uid"]:
        errors.append(f"category_uid: {cls['name']} belongs to category {cls['category_uid']}")
    if isinstance(event.get("activity_id"), int) and event.get("type_uid") != cls["uid"] * 100 + event["activity_id"]:
        errors.append(f"type_uid: must be class_uid * 100 + activity_id = {cls['uid'] * 100 + event['activity_id']}")
    if meta.get("version") != schema.VERSION:
        errors.append(f"metadata.version: this build emits OCSF {schema.VERSION}")
    return errors
