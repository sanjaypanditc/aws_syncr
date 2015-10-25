from aws_syncr.formatter import MergedOptionStringFormatter
from aws_syncr.option_spec.resources import resource_spec
from aws_syncr.errors import BadTemplate

from input_algorithms.spec_base import NotSpecified
from input_algorithms.errors import BadSpecValue
from input_algorithms import spec_base as sb
from input_algorithms.spec_base import Spec
from input_algorithms.dictobj import dictobj
from option_merge import MergedOptions
import six

class only_one_spec(sb.Spec):
    def setup(self, spec):
        self.spec = spec

    def normalise(self, meta, val):
        val = self.spec.normalise(meta, val)
        if type(val) is not list:
            return val

        if len(val) != 1:
            raise BadSpecValue("Please only specify one value", meta=meta)

        return val[0]

class divisible_by_spec(sb.Spec):
    def setup(self, divider):
        self.divider = divider

    def normalise_filled(self, meta, val):
        val = sb.integer_spec().normalise(meta, val)
        if val % self.divider != 0:
            raise BadSpecValue("Value should be divisible by {0}".format(self.divider), meta=meta)
        return val

class function_handler_spec(sb.Spec):
    def normalise_empty(self, meta):
        path = [p for p, _ in meta._path]
        path.pop()
        runtime = meta.everything['.'.join(path)].get("runtime", "python")
        runtime = sb.formatted(sb.string_spec(), formatter=MergedOptionStringFormatter).normalise(meta.at("runtime"), runtime)

        if runtime == 'java':
            raise BadSpecValue("No default function handler for java", meta=meta)
        elif runtime == 'nodejs':
            return "index.handler"
        elif runtime == 'python':
            return "lambda_function.lambda_handler"
        else:
            raise BadSpecValue("No default function handler for {0}".format(runtime), meta=meta)

    def normalise_filled(self, meta, val):
        return sb.formatted(sb.string_spec(), formatter=MergedOptionStringFormatter).normalise(meta, val)

class function_code_spec(sb.Spec):
    def normalise_filled(self, meta, val):
        val = sb.dictof(sb.string_choice_spec(["s3", "inline", "directory"]), sb.any_spec()).normalise(meta, val)
        if not val:
            raise BadSpecValue("Please specify s3, inline or directory for your code", meta=meta)

        if len(val) > 1:
            raise BadSpecValue("Please only specify one of s3, inline or directory for your code", got=list(val.keys()), meta=meta)

        formatted_string = sb.formatted(sb.string_spec(), formatter=MergedOptionStringFormatter)
        if "s3" in val:
            return sb.create_spec(S3Code
                , key = formatted_string
                , bucket = formatted_string
                , version = sb.defaulted(sb.string_spec(), NotSpecified)
                ).normalise(meta, val['s3'])
        elif "inline" in val:
            path = [p for p, _ in meta._path]
            path.pop()
            runtime = meta.everything['.'.join(path)].get("runtime", "python")
            runtime = sb.formatted(sb.string_spec(), formatter=MergedOptionStringFormatter).normalise(meta.at("runtime"), runtime)

            return sb.create_spec(InlineCode
                , code = sb.string_spec()
                , runtime = sb.overridden(runtime)
                ).normalise(meta, {"code": val['inline']})
        else:
            directory = val['directory']
            if isinstance(val['directory'], six.string_types):
                directory = {"directory": val['directory']}

            return sb.create_spec(DirectoryCode
                , directory = sb.directory_spec()
                , exclude = sb.listof(sb.string_spec())
                ).normalise(meta, directory)

class lambdas_spec(Spec):
    def normalise(self, meta, val):
        if 'use' in val:
            template = val['use']
            if template not in meta.everything['templates']:
                available = list(meta.everything['templates'].keys())
                raise BadTemplate("Template doesn't exist!", wanted=template, available=available, meta=meta)

            val = MergedOptions.using(meta.everything['templates'][template], val)

        formatted_string = sb.formatted(sb.string_or_int_as_string_spec(), MergedOptionStringFormatter, expected_type=six.string_types)
        function_name = meta.key_names()['_key_name_0']

        return sb.create_spec(Lambda
            , name = sb.overridden(function_name)
            , role = sb.required(only_one_spec(resource_spec("lambda", function_name, only=["iam"])))
            , code = sb.required(function_code_spec())
            , handler = function_handler_spec()
            , timeout = sb.integer_spec()
            , runtime = sb.required(formatted_string)
            , location = sb.required(formatted_string)
            , description = formatted_string
            , sample_event = sb.string_spec()
            , memory_size = sb.defaulted(divisible_by_spec(64), 128)
            ).normalise(meta, val)

class Lambdas(dictobj):
    fields = ['items']

    def sync_one(self, aws_syncr, amazon, function):
        """Make sure this function exists and has only attributes we want it to have"""
        function_info = amazon.lambdas.function_info(function.name, function.location)
        if not function_info:
            amazon.lambdas.create_function(function.name, function.description, function.location, function.runtime, function.role, function.handler, function.timeout, function.memory_size, function.code)
        else:
            amazon.lambdas.modify_function(function_info, function.name, function.description, function.location, function.runtime, function.role, function.handler, function.timeout, function.memory_size, function.code)

class Lambda(dictobj):
    fields = {
          'name': "Alias of the function"
        , 'role': "The role assumed by the function"
        , 'code': "Code for the function!"
        , 'handler': "Function within your code that gets executed"
        , 'timeout': "Max function execution time"
        , 'runtime': "Runtime environment for the function"
        , 'location': "The region the function exists in"
        , 'description': "Description of the function"
        , 'sample_event': "A sample event to test with"
        , 'memory_size': "Max memory size for the function"
        }

class S3Code(dictobj):
    fields = ["key", "bucket", "version"]

    @property
    def s3_address(self):
        return "s3://{0}/{1}".format(self.bucket, self.key)

class InlineCode(dictobj):
    fields = ["code", "runtime"]
    s3_address = None

class DirectoryCode(dictobj):
    fields = ["directory", "exclude"]
    s3_address = None

def __register__():
    return {"lambda": sb.container_spec(Lambdas, sb.dictof(sb.string_spec(), lambdas_spec()))}

