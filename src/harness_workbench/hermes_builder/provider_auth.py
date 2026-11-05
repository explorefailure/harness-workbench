"""Explicit endpoint/provider selection and named credentials; no account copy."""
import os
import re
from urllib.parse import urlsplit

SELECTED_KEY = 'HWB_SELECTED_PROVIDER_KEY'


def validate(provider, native_names):
    if not isinstance(provider,dict):raise ValueError('provider must be an object')
    mode=provider.get('mode')
    if mode=='scripted':
        if set(provider)!={'mode'}:raise ValueError('scripted provider accepts only mode')
        return
    if mode not in ('local','compatible','native'):
        raise ValueError('provider mode must be scripted, local, compatible or native')
    required={'mode','base_url','model'}
    allowed=required.copy()
    if mode in ('compatible','native'):allowed|={'api_key_env','context_window','max_tokens'}
    if mode=='native':required|={'name','api_key_env'};allowed|={'name'}
    if not required<=set(provider) or set(provider)-allowed:
        raise ValueError('invalid provider fields; required: '+', '.join(sorted(required)))
    endpoint=provider['base_url'];model=provider['model']
    if not isinstance(endpoint,str):raise ValueError('base_url must be a string')
    url=urlsplit(endpoint)
    if (url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment
            or any(c.isspace() for c in endpoint)):
        raise ValueError('base_url must be HTTP(S) without credentials, query or fragment')
    if not isinstance(model,str) or not model or any(c.isspace() for c in model) or model.startswith('-'):
        raise ValueError('model must be a nonempty ID without whitespace')
    if mode=='native' and (not isinstance(provider['name'],str) or provider['name'] not in native_names):
        raise ValueError('native provider name must be one of: '+', '.join(sorted(native_names)))
    if 'api_key_env' in provider:
        key=provider['api_key_env']
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key):
            raise ValueError('api_key_env must name an environment variable')
    for key,default in (('context_window',32768),('max_tokens',4096)):
        value=provider.get(key,default)
        if type(value) is not int or value<=0:raise ValueError(key+' must be a positive integer')
    if provider.get('max_tokens',4096)>provider.get('context_window',32768):
        raise ValueError('max_tokens must not exceed context_window')


def resolve(provider):
    name=provider.get('api_key_env')
    if name is None:return {},[]
    value=os.environ.get(name)
    if not value or not value.strip():raise ValueError('selected credential environment variable is missing or empty: '+name)
    if '\r' in value or '\n' in value or len(value)<8:
        raise ValueError('selected credential must be a single-line value of at least 8 characters')
    return {SELECTED_KEY:value},[value]
