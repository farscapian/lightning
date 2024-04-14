from typing import TextIO, Tuple, Dict, Any
from textwrap import dedent, indent
import logging
import sys
import re

from msggen.model import (ArrayField, CompositeField, EnumField,
                          PrimitiveField, Service, Method)
from msggen.gen.generator import IGenerator

logger = logging.getLogger(__name__)

# The following words need to be changed, otherwise they'd clash with
# built-in keywords.
keywords = ["in", "type"]

# A map of schema type to rust primitive types.
typemap = {
    'boolean': 'bool',
    'hex': 'String',
    'msat': 'Amount',
    'msat_or_all': 'AmountOrAll',
    'msat_or_any': 'AmountOrAny',
    'currency': 'String',
    'number': 'f64',
    'pubkey': 'PublicKey',
    'short_channel_id': 'ShortChannelId',
    'signature': 'String',
    'string': 'String',
    'txid': 'String',
    'float': 'f32',
    'utxo': 'Utxo',
    'feerate': 'Feerate',
    'outpoint': 'Outpoint',
    'outputdesc': 'OutputDesc',
    'hash': 'Sha256',
    'secret': 'Secret',
    'bip340sig': 'String',
    'integer': 'i64',
}

header = f"""#![allow(non_camel_case_types)]
//
// This file was automatically generated using the following command:
//
// ```bash
// {" ".join(sys.argv)}
// ```
//
// Do not edit this file, it'll be overwritten. Rather edit the schema that
// this file was generated from

//! A collection of models to describe [requests] and [responses].
//!
"""


def normalize_varname(field):
    """Make sure that the variable name of this field is valid.
    """
    # Dashes are not valid names
    field.path = field.path.replace("-", "_")
    field.path = re.sub(r'(?<!^)(?=[A-Z])', '_', field.path).lower()
    return field


def gen_field(field, meta):
    if field.omit():
        return ("", "")
    if isinstance(field, CompositeField):
        return gen_composite(field, meta)
    elif isinstance(field, EnumField):
        return gen_enum(field, meta)
    elif isinstance(field, ArrayField):
        return gen_array(field, meta)
    elif isinstance(field, PrimitiveField):
        return gen_primitive(field)
    else:
        raise TypeError(f"Unmanaged type {field}")


def gen_enum(e, meta):
    defi, decl = "", ""

    if e.omit():
        return "", ""

    if e.description != "":
        decl += f"/// {e.description}\n"

    if e.deprecated:
        decl += "#[deprecated]\n"
    decl += f"#[derive(Copy, Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]\npub enum {e.typename} {{\n"

    m = meta['grpc-field-map']
    m2 = meta['grpc-enum-map']

    message_name = e.typename.name
    assert not (message_name in m and message_name in m2)
    if message_name in m:
        m = m[message_name]
    elif message_name in m2:
        m = m2[message_name]
    else:
        m = {}

    complete_variants = True
    for v in e.variants:
        if str(v) not in m:
            complete_variants = False

    if m != {} and complete_variants:
        sorted_variants = sorted(e.variants, key=lambda x: m[str(x)])
        for v in sorted_variants:
            if v is None:
                continue
            norm = v.normalized()
            decl += f"    #[serde(rename = \"{v}\")]\n"
            decl += f"    {norm} = {m[str(v)]},\n"
    decl += "}\n\n"

    # Implement From<i32> so we can convert from the numerical
    # representation
    decl += dedent(f"""\
    impl TryFrom<i32> for {e.typename} {{
        type Error = anyhow::Error;
        fn try_from(c: i32) -> Result<{e.typename}, anyhow::Error> {{
            match c {{
    """)

    if m != {} and complete_variants:
        for v in sorted_variants:
            norm = v.normalized()
            # decl += f"    #[serde(rename = \"{v}\")]\n"
            decl += f"    {m[str(v)]} => Ok({e.typename}::{norm}),\n"
    else:
        for i, v in enumerate(e.variants):
            norm = v.normalized()
            # decl += f"    #[serde(rename = \"{v}\")]\n"
            decl += f"    {i} => Ok({e.typename}::{norm}),\n"

    decl += dedent(f"""\
                o => Err(anyhow::anyhow!("Unknown variant {{}} for enum {e.typename}", o)),
            }}
        }}
    }}

    """)

    # Implement ToString for enums so we can print them nicely as they
    # appear in the schemas.
    decl += dedent(f"""\
    impl ToString for {e.typename} {{
        fn to_string(&self) -> String {{
            match self {{
    """)
    for v in e.variants:
        norm = v.normalized()
        decl += f"            {e.typename}::{norm} => \"{norm}\",\n"
    decl += dedent(f"""\
            }}.to_string()
        }}
    }}

    """)

    typename = e.typename

    if e.override() is not None:
        decl = ""  # No declaration if we have an override
        typename = e.override()

    if not e.optional:
        defi = f"    // Path `{e.path}`\n"
        defi += rename_if_necessary(str(e.name), e.name.normalized())
        defi += f"    pub {e.name.normalized()}: {typename},\n"
    else:
        defi = f"    #[serde(skip_serializing_if = \"Option::is_none\")]\n"
        defi += f"    pub {e.name.normalized()}: Option<{typename}>,\n"

    return defi, decl


def gen_primitive(p):
    defi, decl = "", ""
    org = p.name.name
    typename = typemap.get(p.typename, p.typename)
    normalize_varname(p)

    if p.deprecated:
        defi += "    #[deprecated]\n"
    defi += rename_if_necessary(org, p.name.name)
    if not p.optional:
        defi += f"    pub {p.name}: {typename},\n"
    else:
        defi += f"    #[serde(skip_serializing_if = \"Option::is_none\")]\n    pub {p.name}: Option<{typename}>,\n"

    return defi, decl


def rename_if_necessary(original, name):
    if original != name:
        return f"    #[serde(rename = \"{original}\")]\n"
    else:
        return f""


def gen_array(a, meta):
    name = a.name.normalized().replace("[]", "")
    logger.debug(f"Generating array field {a.name} -> {name} ({a.path})")
    _, decl = gen_field(a.itemtype, meta)

    if a.override():
        decl = ""  # No declaration if we have an override
        itemtype = a.override()
    elif isinstance(a.itemtype, PrimitiveField):
        itemtype = a.itemtype.typename
    elif isinstance(a.itemtype, CompositeField):
        itemtype = a.itemtype.typename
    elif isinstance(a.itemtype, EnumField):
        itemtype = a.itemtype.typename

    if itemtype is None:
        return ("", "")  # Override said not to include

    itemtype = typemap.get(itemtype, itemtype)
    alias = a.name.normalized()
    defi = ""
    if a.deprecated:
        defi += "    #[deprecated]\n"
    defi += rename_if_necessary(alias, name)
    if not a.optional:
        defi += f"    pub {name}: {'Vec<'*a.dims}{itemtype}{'>'*a.dims},\n"
    else:
        defi += f"    #[serde(skip_serializing_if = \"crate::is_none_or_empty\")]\n    pub {name}: Option<{'Vec<'*a.dims}{itemtype}{'>'*a.dims}>,\n"

    return (defi, decl)


def gen_composite(c, meta) -> Tuple[str, str]:
    logger.debug(f"Generating composite field {c.name} ({c.path})")
    fields = []
    for f in c.fields:
        fields.append(gen_field(f, meta))
    fields = sorted(fields)

    r = "".join([f[1] for f in fields])

    r += f"""#[derive(Clone, Debug, Deserialize, Serialize)]\npub struct {c.typename} {{\n"""

    r += "".join([f[0] for f in fields])

    r += "}\n\n"

    defi = ""
    if c.deprecated:
        defi += "    #[deprecated]\n"
    if not c.optional:
        defi += f"    pub {c.name}: {c.typename},\n"
    else:
        defi += f"    #[serde(skip_serializing_if = \"Option::is_none\")]\n    pub {c.name}: Option<{c.typename}>,\n"

    return defi, r


class RustGenerator(IGenerator):
    def __init__(self, dest: TextIO, meta: Dict[str, Any]):
        self.dest = dest
        self.meta = meta

    def write(self, text: str, numindent: int = 0) -> None:
        raw = dedent(text)
        if numindent > 0:
            raw = indent(text, "\t" * numindent)
        self.dest.write(raw)

    def generate_requests(self, service: Service):
        self.write("""\
        pub mod requests {
            #[allow(unused_imports)]
            use crate::primitives::*;
            #[allow(unused_imports)]
            use serde::{{Deserialize, Serialize}};
            use core::fmt::Debug;
            use super::{IntoRequest, Request, TypedRequest};
        """)

        for meth in service.methods:
            req = meth.request
            _, decl = gen_composite(req, self.meta)
            self.write(decl, numindent=1)
            self.generate_request_trait_impl(meth)

        self.write("}\n\n")

    def generate_request_trait_impl(self, method: Method):
        self.write(dedent(f"""\
        impl From<{method.request.typename}> for Request {{
            fn from(r: {method.request.typename}) -> Self {{
                Request::{method.name}(r)
            }}
        }}

        impl IntoRequest for {method.request.typename} {{
            type Response = super::responses::{method.response.typename};
        }}

        impl TypedRequest for {method.request.typename} {{
            type Response = super::responses::{method.response.typename};

            fn method(&self) -> &str {{
                "{method.name.lower()}"
            }}
        }}
        """), numindent=1)

    def generate_responses(self, service: Service):
        self.write("""
        pub mod responses {
            #[allow(unused_imports)]
            use crate::primitives::*;
            #[allow(unused_imports)]
            use serde::{{Deserialize, Serialize}};
            use super::{TryFromResponseError, Response};

        """)

        for meth in service.methods:
            res = meth.response
            _, decl = gen_composite(res, self.meta)
            self.write(decl, numindent=1)
            self.generate_response_trait_impl(meth)

        self.write("}\n\n")

    def generate_response_trait_impl(self, method: Method):
        self.write(dedent(f"""\
        impl TryFrom<Response> for {method.response.typename} {{
            type Error = super::TryFromResponseError;

            fn try_from(response: Response) -> Result<Self, Self::Error> {{
                match response {{
                    Response::{method.name}(response) => Ok(response),
                    _ => Err(TryFromResponseError)
                }}
            }}
        }}

        """), numindent=1)

    def generate_enums(self, service: Service):
        """The Request and Response enums serve as parsing primitives.
        """
        self.write(f"""\
        use serde::{{Deserialize, Serialize}};

        #[derive(Clone, Debug, Serialize, Deserialize)]
        #[serde(tag = "method", content = "params")]
        #[serde(rename_all = "lowercase")]
        pub enum Request {{
        """)

        for method in service.methods:
            self.write(f"{method.name}(requests::{method.request.typename}),\n", numindent=1)

        self.write(f"""\
        }}

        #[derive(Clone, Debug, Serialize, Deserialize)]
        #[serde(tag = "method", content = "result")]
        #[serde(rename_all = "lowercase")]
        pub enum Response {{
        """)

        for method in service.methods:
            self.write(f"{method.name}(responses::{method.response.typename}),\n", numindent=1)

        self.write(f"""\
        }}

        """)

    def generate_request_trait(self):
        self.write("""
        pub trait IntoRequest: Into<Request> {
            type Response: TryFrom<Response, Error = TryFromResponseError>;
        }

        pub trait TypedRequest {
            type Response;

            fn method(&self) -> &str;
        }

        #[derive(Debug)]
        pub struct TryFromResponseError;

        """)

    def generate(self, service: Service) -> None:
        self.write(header)

        self.generate_enums(service)

        self.generate_request_trait()

        self.generate_requests(service)
        self.generate_responses(service)
