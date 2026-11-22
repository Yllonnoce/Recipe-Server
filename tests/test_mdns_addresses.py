"""Which addresses the printer is announced at."""
from recipelib.ipp.mdns import local_ipv4s, pick_ipv4s

DOCKER = ["172.17.0.1", "172.18.0.1", "172.19.0.1"]


def test_only_the_lan_address_is_announced_when_the_machine_also_has_docker_bridges():
    # The host name resolves to every interface. A phone that is handed a Docker
    # bridge address cannot reach the printer and stops listing it.
    assert pick_ipv4s("10.0.0.77", ["10.0.0.77", *DOCKER]) == ["10.0.0.77"]


def test_the_lan_address_is_announced_even_if_the_host_name_does_not_resolve_to_it():
    assert pick_ipv4s("192.168.1.20", DOCKER) == ["192.168.1.20"]
    assert pick_ipv4s("192.168.1.20", []) == ["192.168.1.20"]


def test_without_a_route_the_host_names_addresses_are_used():
    assert pick_ipv4s(None, ["192.168.1.20", "10.0.0.5"]) == ["10.0.0.5", "192.168.1.20"]


def test_loopback_and_self_assigned_addresses_are_never_announced():
    assert pick_ipv4s(None, ["127.0.0.1", "127.0.1.1", "169.254.7.7", "10.0.0.5"]) == ["10.0.0.5"]
    assert pick_ipv4s("127.0.0.1", ["10.0.0.5"]) == ["10.0.0.5"]
    assert pick_ipv4s("169.254.7.7", []) == []
    assert pick_ipv4s(None, []) == []


def test_this_machine_announces_at_most_its_routed_address():
    import socket
    ips = local_ipv4s()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        routed = s.getsockname()[0]
        s.close()
    except OSError:
        return      # no network here: nothing to compare with
    if not routed.startswith(("127.", "169.254.")):
        assert ips == [routed]
