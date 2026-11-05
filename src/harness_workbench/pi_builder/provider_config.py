"""Explicit native Pi provider configuration; no inference client or account copy."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from harness_workbench.capture import redact_bytes

SECRET_ENV = 'HWB_SELECTED_PROVIDER_KEY'


def validate(provider):
    if not isinstance(provider, dict):
        raise ValueError('provider must be an object')
    mode = provider.get('mode')
    if mode not in ('compatible', 'native'):
        raise ValueError('provider mode must be compatible or native')
    allowed = {'mode', 'model', 'api_key_env', 'base_url'}
    allowed |= {'api', 'context_window', 'max_tokens'} if mode == 'compatible' else {'name'}
    if set(provider) - allowed:
        raise ValueError('unsupported provider field(s): ' + ', '.join(sorted(set(provider) - allowed)))
    model = provider.get('model')
    if not isinstance(model, str) or not model or any(c.isspace() for c in model) or model.startswith('-'):
        raise ValueError('provider model must be a nonempty ID without whitespace')
    if mode == 'native' and not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', provider.get('name', '')):
        raise ValueError('native provider requires a provider name')
    endpoint = provider.get('base_url')
    if mode == 'compatible' and endpoint is None:
        raise ValueError('compatible provider requires base_url')
    if endpoint is not None:
        if not isinstance(endpoint, str):
            raise ValueError('base_url must be a string')
        url = urlsplit(endpoint)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(c.isspace() for c in endpoint)):
            raise ValueError('base_url must be HTTP(S) without credentials, query or fragment')
    if 'api_key_env' in provider:
        name = provider['api_key_env']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError('api_key_env must name an environment variable, not a credential value')
    elif mode == 'native':
        raise ValueError('native provider requires an explicit api_key_env reference')
    if mode == 'compatible':
        if provider.get('api', 'openai-completions') not in ('openai-completions', 'openai-responses', 'anthropic-messages', 'google-generative-ai'):
            raise ValueError('unsupported compatible provider API')
        for key, default in (('context_window', 32768), ('max_tokens', 4096)):
            value = provider.get(key, default)
            if type(value) is not int or value <= 0:
                raise ValueError(key + ' must be a positive integer')
        if provider.get('max_tokens', 4096) > provider.get('context_window', 32768):
            raise ValueError('max_tokens must not exceed context_window')
    return provider


def prepare(provider, private_dir):
    validate(provider)
    name = provider.get('name', 'hwb-compatible')
    values = []
    env = {}
    if 'api_key_env' in provider:
        secret = os.environ.get(provider['api_key_env'])
        if not secret or not secret.strip():
            raise ValueError('selected credential environment variable is missing or empty: ' + provider['api_key_env'])
        if '\n' in secret or '\r' in secret or len(secret) < 8:
            raise ValueError('selected credential must be a single-line value of at least 8 characters')
        env[SECRET_ENV] = secret
        values = [secret]
    config = {'apiKey': '${' + SECRET_ENV + '}' if values else 'hwb-local-no-credential'}
    if 'base_url' in provider:
        config['baseUrl'] = provider['base_url']
    if provider['mode'] == 'compatible':
        config.update(api=provider.get('api', 'openai-completions'), authHeader=True,
            models=[{'id': provider['model'], 'reasoning': False, 'input': ['text'],
                'contextWindow': provider.get('context_window', 32768),
                'maxTokens': provider.get('max_tokens', 4096)}])
    private_dir.mkdir(parents=True, exist_ok=True)
    (private_dir / 'models.json').write_text(json.dumps({'providers': {name: config}}, indent=2) + '\n')
    (private_dir / 'models.json').chmod(0o600)
    return name, env, values


def sanitize(raw, values):
    return redact_bytes(raw, values)[0] if values else raw
