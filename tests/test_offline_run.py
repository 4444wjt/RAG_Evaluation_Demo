"""本机无外网试跑的出站守卫：回环放行，外部地址拒绝并记录。全部离线。"""

import json
import socket

import pytest

from tools.competition import offline_run


@pytest.mark.parametrize('host, expected', [
    ('127.0.0.1', True), ('127.0.0.53', True), ('::1', True), ('[::1]', True), ('localhost', True),
    (None, True), (b'127.0.0.1', True),
    ('api.deepseek.com', False), ('10.0.0.5', False), ('8.8.8.8', False), ('huggingface.co', False),
])
def test_loopback_predicate(host, expected):
    assert offline_run.is_loopback(host) is expected


def test_guard_allows_loopback_and_records_blocked_targets(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k: calls.append((host, port)) or ['ok'])
    monkeypatch.setattr(socket, 'create_connection', lambda address, *a, **k: calls.append(address) or 'conn')
    log = tmp_path / 'blocked.jsonl'
    guard = offline_run.Guard(log)
    guard.install()

    assert socket.getaddrinfo('localhost', 11434) == ['ok']
    assert socket.create_connection(('127.0.0.1', 8000)) == 'conn'
    with pytest.raises(OSError):
        socket.getaddrinfo('huggingface.co', 443)
    with pytest.raises(OSError):
        socket.create_connection(('203.0.113.7', 443))

    assert calls == [('localhost', 11434), ('127.0.0.1', 8000)]
    rows = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    assert [(row['call'], row['host'], row['port']) for row in rows] == [
        ('getaddrinfo', 'huggingface.co', 443), ('create_connection', '203.0.113.7', 443)]
    assert guard.blocked == 2
    # 记录里的代码位置是相对路径，不暴露本机目录。
    assert all(':' in row['origin'] and not row['origin'][1:3] == ':/' for row in rows)
    assert str(offline_run.ROOT) not in log.read_text(encoding='utf-8')


def test_gateway_refuses_non_loopback_upstream():
    with pytest.raises(SystemExit):
        offline_run.make_gateway('http://203.0.113.7:11434')
    with pytest.raises(SystemExit):
        offline_run.make_gateway('https://127.0.0.1:11434')
