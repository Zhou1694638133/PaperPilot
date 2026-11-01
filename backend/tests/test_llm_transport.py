import socket
import ssl
import httpx
import pytest
from app import llm
from app.config import Settings


@pytest.mark.parametrize('outer,cause,code', [
    (httpx.ConnectError, ConnectionRefusedError('private-proxy-password'), 'connection_refused'),
    (httpx.ConnectError, socket.gaierror(-2, 'private-endpoint'), 'dns'),
    (httpx.ConnectError, ssl.SSLCertVerificationError(1, 'private-certificate'), 'tls_certificate'),
    (httpx.ConnectError, ssl.SSLError(1, 'private-tls'), 'tls_handshake'),
    (httpx.ConnectTimeout, None, 'connect_timeout'),
    (httpx.ProxyError, None, 'proxy'),
])
def test_transport_diagnostics_are_precise_redacted_and_not_retried(monkeypatch, outer, cause, code):
    original = httpx.Client
    calls = []
    def handler(request):
        calls.append(request)
        error = outer('private-key-and-url', request=request)
        error.__cause__ = cause
        raise error
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(llm_api_key='private-key', llm_model='model', llm_base_url='https://example.org/v1')
    with pytest.raises(llm.ModelError) as exc:
        llm.request_json('test', {}, 'test', settings=settings)
    assert code in str(exc.value)
    assert 'private' not in str(exc.value)
    assert not exc.value.ambiguous
    assert len(calls) == 1


def test_env_proxy_setting_is_passed_to_transport_without_modifying_environment(monkeypatch):
    original = httpx.Client
    options = []
    def client(**kw):
        options.append(kw['trust_env'])
        return original(transport=httpx.MockTransport(lambda _: httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})), **kw)
    monkeypatch.setattr(llm.httpx, 'Client', client)
    monkeypatch.setenv('LLM_TRUST_ENV', 'false')
    settings=Settings(llm_api_key='test',llm_model='model',llm_base_url='https://example.org/v1')
    assert llm.request_json('test',{},'id',settings=settings)[0]['ok']
    assert options == [False]


def test_read_timeout_still_requires_cost_confirmation(monkeypatch):
    original=httpx.Client
    def handler(request):raise httpx.ReadTimeout('private-key',request=request)
    monkeypatch.setattr(llm.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(llm.ModelError) as exc:
        llm.request_json('test',{},'id',settings=Settings(llm_api_key='test',llm_model='model',llm_base_url='https://example.org/v1'))
    assert exc.value.ambiguous and 'ReadTimeout' in str(exc.value)
    assert 'private-key' not in str(exc.value)
