"""Reviewed JSON contracts and bounded I/O adapters for direct research calls.

This module is copied into generated servers. It never evaluates contract text.
It provides input/output adaptation, not scientific algorithms or a code sandbox.
"""
from __future__ import annotations

import copy
import keyword
import math
from pathlib import Path


class ContractError(ValueError):
    pass


SCHEMA_KEYS = {'type', 'anyOf', 'enum', 'const', 'properties', 'required', 'prefixItems',
               'additionalProperties', 'items', 'minItems', 'maxItems',
               'minLength', 'maxLength', 'minimum', 'maximum',
               'exclusiveMinimum', 'exclusiveMaximum', 'description', 'uniqueItems'}
TYPES = {'null', 'boolean', 'integer', 'number', 'string', 'array', 'object'}


def check_schema(schema, depth=0):
    if depth > 20 or not isinstance(schema, dict) or not schema:
        raise ContractError('Input schema must be a nonempty bounded JSON Schema object.')
    if set(schema) - SCHEMA_KEYS:
        raise ContractError('Unsupported schema keywords: ' + ', '.join(sorted(set(schema) - SCHEMA_KEYS)))
    if 'anyOf' in schema:
        if set(schema) - {'anyOf', 'description'} or not isinstance(schema['anyOf'], list) or not 1 <= len(schema['anyOf']) <= 8:
            raise ContractError('anyOf must contain 1–8 schemas and no sibling validation keywords.')
        for branch in schema['anyOf']:
            check_schema(branch, depth + 1)
        return
    if schema.get('type') not in TYPES:
        raise ContractError('Every input schema must declare a supported JSON type.')
    typ = schema['type']
    relevant = {'type', 'enum', 'const', 'description'}
    relevant |= {'number': {'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'},
                 'integer': {'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'},
                 'string': {'minLength', 'maxLength'},
                 'array': {'items', 'prefixItems', 'minItems', 'maxItems', 'uniqueItems'},
                 'object': {'properties', 'required', 'additionalProperties'}}.get(typ, set())
    if set(schema) - relevant:
        raise ContractError('Schema keyword does not apply to its declared type.')
    for key in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise ContractError('Numeric schema bounds must be finite numbers.')
    for key in ('minLength', 'maxLength', 'minItems', 'maxItems'):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ContractError('Length schema bounds must be nonnegative integers.')
    if 'description' in schema and not isinstance(schema['description'], str):
        raise ContractError('Schema description must be text.')
    if 'uniqueItems' in schema and type(schema['uniqueItems']) is not bool:
        raise ContractError('uniqueItems must be boolean.')
    if typ == 'array':
        if 'prefixItems' in schema:
            prefix = schema['prefixItems']
            if not isinstance(prefix, list) or not prefix or schema.get('items') is not False or schema.get('minItems') != len(prefix) or schema.get('maxItems') != len(prefix):
                raise ContractError('Tuple arrays require fixed prefixItems, exact length, and items:false.')
            for item in prefix:
                check_schema(item, depth + 1)
        else:
            check_schema(schema.get('items'), depth + 1)
    if typ == 'object':
        if not isinstance(schema.get('properties'), dict) or schema.get('additionalProperties') is not False:
            raise ContractError('Object schemas require explicit properties and additionalProperties:false.')
        for key, value in schema['properties'].items():
            if not isinstance(key, str) or not key:
                raise ContractError('Object property names must be nonempty strings.')
            check_schema(value, depth + 1)
        required = schema.get('required', [])
        if not isinstance(required, list) or any(not isinstance(x, str) or x not in schema['properties'] for x in required) or len(set(required)) != len(required):
            raise ContractError('Object required properties must exist and be unique.')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']):
        raise ContractError('enum must be a nonempty JSON array.')
    for value in schema.get('enum', []) + ([schema['const']] if 'const' in schema else []):
        validate_value(value, {k: v for k, v in schema.items() if k not in {'enum', 'const'}})


def _equal(a, b):
    """JSON equality without Python's True == 1 ambiguity."""
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_equal(a[k], b[k]) for k in a)
    return a == b


def validate_value(value, schema, path='input'):
    if 'anyOf' in schema:
        for branch in schema['anyOf']:
            try:
                validate_value(value, branch, path)
                return
            except ContractError:
                pass
        raise ContractError(path + ': value does not match any declared schema branch.')
    typ = schema['type']
    okay = {'null': value is None, 'boolean': type(value) is bool,
            'integer': type(value) is int, 'number': type(value) in (int, float),
            'string': type(value) is str, 'array': type(value) is list,
            'object': type(value) is dict}[typ]
    if not okay:
        raise ContractError(path + ': expected ' + typ + '; coercion is disabled.')
    if 'enum' in schema and not any(_equal(value, x) for x in schema['enum']):
        raise ContractError(path + ': value is outside the reviewed enum.')
    if 'const' in schema and not _equal(value, schema['const']):
        raise ContractError(path + ': value differs from the reviewed constant.')
    if typ in {'number', 'integer'}:
        if not math.isfinite(value):
            raise ContractError(path + ': nonfinite input is not permitted.')
        for key, fails in [('minimum', lambda x: value < x), ('maximum', lambda x: value > x),
                           ('exclusiveMinimum', lambda x: value <= x), ('exclusiveMaximum', lambda x: value >= x)]:
            if key in schema and fails(schema[key]):
                raise ContractError(path + ': violates ' + key + '.')
    if typ in {'string', 'array'}:
        lo, hi = ('minItems', 'maxItems') if typ == 'array' else ('minLength', 'maxLength')
        if lo in schema and len(value) < schema[lo] or hi in schema and len(value) > schema[hi]:
            raise ContractError(path + ': length is outside the reviewed bounds.')
    if typ == 'array':
        if schema.get('uniqueItems') and any(_equal(value[i], value[j]) for i in range(len(value)) for j in range(i)):
            raise ContractError(path + ': array entries must be unique.')
        for i, item in enumerate(value):
            child = schema['prefixItems'][i] if 'prefixItems' in schema else schema['items']
            validate_value(item, child, f'{path}[{i}]')
    if typ == 'object':
        if set(value) - set(schema['properties']):
            if schema.get('additionalProperties') is False:
                raise ContractError(path + ': undeclared object properties are not permitted.')
        if set(schema.get('required', [])) - set(value):
            raise ContractError(path + ': missing required object properties.')
        for key, item in value.items():
            child = schema['properties'].get(key, schema.get('additionalProperties'))
            if isinstance(child, dict):
                validate_value(item, child, path + '.' + key)


def _field(values, field):
    if not isinstance(field, str) or not field or any(not x for x in field.split('.')):
        raise ContractError('Rule field must be a dotted object property path.')
    current = values
    for part in field.split('.'):
        if not isinstance(current, dict) or part not in current:
            raise ContractError('Rule field is missing: ' + field)
        current = current[part]
    return current


def _expr(spec, values, validate_only=False, depth=0):
    """Small integer/count DSL for source-backed shape preconditions, never eval."""
    if depth > 12:
        raise ContractError('Rule expression is too deep.')
    if type(spec) is int:
        return spec
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ContractError('Invalid bounded rule expression.')
    op, argument = next(iter(spec.items()))
    if op == 'value':
        if not isinstance(argument, str) or not argument:
            raise ContractError('Rule value requires a field path.')
        result = 1 if validate_only else _field(values, argument)
        if type(result) is not int:
            raise ContractError('Count expression requires an integer.')
        return result
    if op in {'length', 'unique_count'}:
        if not isinstance(argument, str) or not argument:
            raise ContractError('Count expression requires a field path.')
        result = [] if validate_only else _field(values, argument)
        if not isinstance(result, list):
            raise ContractError('Count expression requires an array.')
        return len(result) if op == 'length' else len(set(result))
    if op in {'add', 'multiply'}:
        if not isinstance(argument, list) or not 1 <= len(argument) <= 8:
            raise ContractError('Arithmetic count expression requires 1–8 operands.')
        result = 0 if op == 'add' else 1
        for item in argument:
            value = _expr(item, values, validate_only, depth + 1)
            result = result + value if op == 'add' else result * value
        return result
    if op == 'if':
        if not isinstance(argument, dict) or set(argument) != {'field', 'then', 'else'} or not isinstance(argument['field'], str):
            raise ContractError('Conditional count expression requires field/then/else.')
        if validate_only:
            _expr(argument['then'], values, True, depth + 1)
            _expr(argument['else'], values, True, depth + 1)
            return 1
        condition = _field(values, argument['field'])
        if type(condition) is not bool:
            raise ContractError('Conditional count field must be boolean.')
        return _expr(argument['then' if condition else 'else'], values, False, depth + 1)
    raise ContractError('Unsupported rule expression operation: ' + op)


def check_rules(rules):
    if not isinstance(rules, list) or len(rules) > 32:
        raise ContractError('rules must be a bounded list.')
    for rule in rules:
        if not isinstance(rule, dict):
            raise ContractError('Every rule must be an object.')
        op = rule.get('op')
        keys = {'length_equals': {'op', 'field', 'count'}, 'length_multiple': {'op', 'field', 'divisor'},
                'ordered_pairs': {'op', 'field'}, 'finite_moments': {'op', 'field', 'require_positive_std'}}.get(op)
        if keys is None or set(rule) != keys or not isinstance(rule.get('field'), str) or not rule['field']:
            raise ContractError('Unsupported declarative rule or keys.')
        if op == 'finite_moments':
            if type(rule['require_positive_std']) is not bool:
                raise ContractError('finite_moments requires an explicit boolean require_positive_std.')
        elif op != 'ordered_pairs':
            _expr(rule['count' if op == 'length_equals' else 'divisor'], {}, True)


def validate_rules(values, rules):
    for rule in rules:
        array = _field(values, rule['field'])
        if not isinstance(array, list):
            raise ContractError('Rule requires an array: ' + rule['field'])
        if rule['op'] == 'finite_moments':
            if not array or any(type(x) not in (int, float) or not math.isfinite(x) for x in array):
                raise ContractError(rule['field'] + ': moments require a finite numeric vector.')
            import numpy as np
            with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
                mean, std = np.mean(array), np.std(array)
            constant = all(x == array[0] for x in array)
            if not np.isfinite(mean) or not np.isfinite(std) or rule['require_positive_std'] and (constant or not std > 0):
                raise ContractError(rule['field'] + ': source mean/std normalization is undefined or nonfinite.')
        elif rule['op'] == 'ordered_pairs':
            if any(not isinstance(row, list) or len(row) != 2 or type(row[0]) not in (float, int) or type(row[1]) not in (float, int) or not row[0] < row[1] for row in array):
                raise ContractError(rule['field'] + ': each bound requires lower < upper.')
        else:
            count = _expr(rule['count' if rule['op'] == 'length_equals' else 'divisor'], values)
            if count < 1 or (len(array) != count if rule['op'] == 'length_equals' else not array or len(array) % count != 0):
                raise ContractError(rule['field'] + ': length violates the declared source precondition.')


def check_parameter(spec):
    if not isinstance(spec, dict) or set(spec) - {'schema', 'adapter'} or 'schema' not in spec:
        raise ContractError('Each parameter requires schema and an optional adapter.')
    check_schema(spec['schema'])
    adapter = spec.get('adapter', {'kind': 'identity'})
    if not isinstance(adapter, dict):
        raise ContractError('Adapter must be an object.')
    kind = adapter.get('kind')
    allowed = {'identity': {'kind'}, 'ndarray': {'kind', 'dtype', 'shape'},
               'dataframe': {'kind', 'columns'}, 'path': {'kind', 'mode', 'return_type'}}.get(kind)
    if allowed is None or set(adapter) - allowed:
        raise ContractError('Unsupported adapter kind or keys.')
    if kind == 'ndarray':
        if spec['schema'].get('type') != 'array' or adapter.get('dtype') not in {'float64', 'int64', 'bool'}:
            raise ContractError('ndarray requires an array schema and float64/int64/bool dtype.')
        shape = adapter.get('shape')
        if not isinstance(shape, list) or not 1 <= len(shape) <= 8 or any(x is not None and (type(x) is not int or x < 0) for x in shape):
            raise ContractError('ndarray shape requires 1–8 integer/null dimensions.')
        node = spec['schema']
        for size in shape:
            if node.get('type') != 'array':
                raise ContractError('ndarray schema nesting must match its declared rank.')
            if size is not None and (node.get('minItems') != size or node.get('maxItems') != size):
                raise ContractError('Fixed ndarray dimensions must also appear as minItems/maxItems in schema.')
            node = node['items']
        expected = {'float64': {'number', 'integer'}, 'int64': {'integer'}, 'bool': {'boolean'}}[adapter['dtype']]
        if node.get('type') not in expected:
            raise ContractError('ndarray leaf schema must match dtype without coercion.')
    if kind == 'dataframe':
        columns = adapter.get('columns')
        if not isinstance(columns, dict) or not columns:
            raise ContractError('DataFrame adapter requires explicit column schemas.')
        for schema in columns.values():
            check_schema(schema)
        schema = spec['schema']
        if schema.get('type') != 'object' or set(schema.get('properties', {})) != {'columns', 'data'} or set(schema.get('required', [])) != {'columns', 'data'}:
            raise ContractError('DataFrame input uses an explicit columns/data JSON object.')
        if schema['properties']['columns'].get('const') != list(columns):
            raise ContractError('DataFrame column order must be fixed in its JSON Schema.')
        rows = schema['properties']['data']
        if rows.get('type') != 'array' or rows['items'].get('type') != 'array' or rows['items'].get('minItems') != len(columns) or rows['items'].get('maxItems') != len(columns) or rows['items'].get('prefixItems') != list(columns.values()):
            raise ContractError('DataFrame rows must declare the exact column count.')
    if kind == 'path':
        if spec['schema'].get('type') != 'string' or adapter.get('mode') not in {'input', 'output'} or adapter.get('return_type', 'path') not in {'str', 'path'}:
            raise ContractError('Path adapter requires string schema, input/output mode, and str/path return_type.')


def check_contract(contract):
    allowed = {'reviewed', 'source_sha256', 'parameters', 'constructor', 'rules', 'result', 'description', 'tool_name'}
    if not isinstance(contract, dict) or set(contract) - allowed or contract.get('reviewed') is not True:
        raise ContractError('Contract requires explicit reviewed:true and supported keys.')
    sha = contract.get('source_sha256')
    if not isinstance(sha, str) or len(sha) != 64 or any(x not in '0123456789abcdef' for x in sha):
        raise ContractError('Reviewed contract requires the exact source SHA-256.')
    params = contract.get('parameters')
    if not isinstance(params, dict):
        raise ContractError('Contract parameters must be an object.')
    for name, spec in params.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ContractError('Parameter names must be Python identifiers.')
        check_parameter(spec)
    if 'constructor' in contract:
        constructor = contract['constructor']
        if not isinstance(constructor, dict) or set(constructor) != {'parameters'} or not isinstance(constructor['parameters'], dict):
            raise ContractError('Constructor requires explicit parameters, including an empty object for no arguments.')
        for name, spec in constructor['parameters'].items():
            if not isinstance(name, str) or not name.isidentifier():
                raise ContractError('Constructor parameter names must be identifiers.')
            check_parameter(spec)
    check_rules(contract.get('rules', []))
    result = contract.get('result', {'nonfinite': 'reject'})
    if not isinstance(result, dict) or set(result) - {'nonfinite', 'nan_regions'} or result.get('nonfinite', 'reject') not in {'reject', 'null_with_mask'}:
        raise ContractError('Result policy must be reject or explicit null_with_mask.')
    regions = result.get('nan_regions')
    if regions is not None:
        if result.get('nonfinite') != 'null_with_mask' or not isinstance(regions, list) or not regions:
            raise ContractError('nan_regions requires the explicit null_with_mask policy.')
        for region in regions:
            if not isinstance(region, dict) or set(region) != {'field', 'region'} or not isinstance(region['field'], str) or region['region'] != 'lower_triangle':
                raise ContractError('Only declared matrix lower_triangle NaN regions are supported.')
    if 'description' in contract and not isinstance(contract['description'], str):
        raise ContractError('Contract description must be text.')
    if 'tool_name' in contract and (not isinstance(contract['tool_name'], str) or not contract['tool_name'].isidentifier() or keyword.iskeyword(contract['tool_name'])):
        raise ContractError('Tool name must be a Python identifier.')
    return copy.deepcopy(contract)


def _shape(value):
    if not isinstance(value, list):
        return ()
    if not value:
        return (0,)
    shapes = [_shape(x) for x in value]
    if any(shape != shapes[0] for shape in shapes):
        raise ContractError('Ragged arrays are not permitted.')
    return (len(value),) + shapes[0]


def _path(relative, base, must_exist):
    rel = Path(relative)
    if not relative or rel.is_absolute() or rel.drive or any(x in {'..', '.'} for x in relative.replace('\\', '/').split('/')) or '\x00' in relative or ':' in relative:
        raise ContractError('File paths must be relative to the managed input/output directory without parent traversal.')
    base = base.resolve()
    raw = base / rel
    current = raw
    while current != base:
        if current.is_symlink():
            raise ContractError('Managed file paths cannot traverse symbolic links.')
        current = current.parent
    path = raw.resolve()
    if base not in path.parents:
        raise ContractError('File path escapes its managed directory.')
    if must_exist and not path.is_file():
        raise ContractError('Managed input file does not exist: ' + relative)
    if not must_exist:
        if path.parent == base and path.name in {'native-result.json', 'serialization-audit.json'}:
            raise ContractError('Output filename is reserved for the call record.')
        if path.exists():
            raise ContractError('Output path already exists.')
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def adapt_inputs(values, specs, workspace, output):
    if set(values) != set(specs):
        raise ContractError('Input parameters do not exactly match the reviewed interface.')
    converted = {}
    for name, spec in specs.items():
        if name not in values:
            raise ContractError('Missing input parameter: ' + name)
        value = values[name]
        validate_value(value, spec['schema'], name)
        adapter = spec.get('adapter', {'kind': 'identity'})
        kind = adapter['kind']
        if kind == 'ndarray':
            shape = _shape(value)
            expected = adapter['shape']
            if len(shape) != len(expected) or any(x is not None and x != y for x, y in zip(expected, shape)):
                raise ContractError(name + ': array shape does not match the reviewed contract.')
            import numpy as np
            value = np.asarray(value, dtype=adapter['dtype'])
        elif kind == 'dataframe':
            for i, row in enumerate(value['data']):
                for column, cell in zip(adapter['columns'], row):
                    validate_value(cell, adapter['columns'][column], f'{name}.data[{i}].{column}')
            import pandas as pd
            value = pd.DataFrame(value['data'], columns=value['columns'])
        elif kind == 'path':
            value = _path(value, workspace / 'inputs' if adapter['mode'] == 'input' else output, adapter['mode'] == 'input')
            if adapter.get('return_type', 'path') == 'str':
                value = str(value)
        converted[name] = value
    return converted


def before_validate(value, schema):
    """Pydantic BeforeValidator hook; retain caller types even under a lax mount."""
    validate_value(value, schema)
    return value


def serialize_result(value, policy=None, output_root=None):
    policy = policy or {'nonfinite': 'reject'}
    mask, artifacts = [], []

    def convert(obj, segments):
        if obj is None or type(obj) in (str, bool, int):
            return obj
        if isinstance(obj, Path):
            path = obj.resolve()
            if output_root is None or output_root.resolve() not in path.parents or not path.is_file() or path.is_symlink():
                raise ContractError('Returned Path is not a regular file inside this call output directory.')
            artifacts.append(str(path))
            return str(path)
        if type(obj) is float:
            if math.isfinite(obj):
                return obj
            allowed = math.isnan(obj) and policy.get('nonfinite') == 'null_with_mask'
            regions = policy.get('nan_regions')
            if allowed and regions is not None:
                allowed = any(len(segments) == 3 and segments[0] == x['field'] and type(segments[1]) is int and type(segments[2]) is int and segments[2] <= segments[1] for x in regions)
            if not allowed:
                raise ContractError('Upstream returned an undeclared nonfinite number; no successful result recorded.')
            path = '/' + '/'.join(str(x).replace('~', '~0').replace('/', '~1') for x in segments)
            mask.append({'path': path, 'kind': 'nan'})
            return None
        if isinstance(obj, (list, tuple)):
            return [convert(x, segments + [i]) for i, x in enumerate(obj)]
        if isinstance(obj, dict):
            if any(not isinstance(k, str) for k in obj):
                raise ContractError('Upstream result has non-string dictionary keys.')
            return {k: convert(v, segments + [k]) for k, v in obj.items()}
        package = type(obj).__module__.split('.')[0]
        if package == 'numpy' and hasattr(obj, 'tolist'):
            return convert(obj.tolist(), segments)
        if package == 'pandas' and type(obj).__name__ == 'DataFrame':
            if not obj.columns.is_unique:
                raise ContractError('Returned DataFrame has duplicate column identifiers.')
            return convert({'columns': obj.columns.tolist(), 'index': obj.index.tolist(), 'data': obj.to_numpy().tolist()}, segments)
        raise ContractError('Upstream result cannot be safely represented as JSON: ' + type(obj).__name__)

    return convert(value, []), mask, artifacts
