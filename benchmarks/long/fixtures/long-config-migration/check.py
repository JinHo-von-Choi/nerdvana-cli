"""Checks the config migration. Exit status 0 means every file is right."""

import hashlib
import json
import pathlib
import sys
import tomllib

EXPECTED = {'orders-api': {'app': 'c2a0a418bb1214ec', 'logging': 'fb479c4d3a772817', 'db': '0951909dd6b21196', 'security': '095c782d43057095', 'limits': 'ca0732c2bb83803d', 'flags': '70c3c5dd48e90fe4', 'environment': '69c10d222d173781', 'route': '330c279d53d756e3'}, 'billing-worker': {'app': '650aa17dcd18f4d8', 'logging': 'b61f9713f79bb558', 'db': 'a199d0d0f6f19e7c', 'security': '9de2c8c56725ed4a', 'limits': '8ddd7d635d9b1fc6', 'environment': 'ae21527dd4143ee8', 'route': 'edf88120517165f8'}, 'search-gateway': {'app': 'bb56dae8b46e9b68', 'logging': 'baef3497973a9249', 'db': '9169cc16f5466c5d', 'cache': '157639179f3067ed', 'security': 'fadd59f6c3e2cc23', 'limits': 'de78705f15b98b4a', 'flags': '86fba6c7ef60768f', 'environment': '8fa8c0d14401006c', 'route': '416c9b72273acdb3'}, 'auth-proxy': {'app': 'f6baddbf57458bd2', 'logging': 'd4bcc57621aeddd1', 'db': '629a0dc0c3cb29f6', 'cache': '6e20f07536c43411', 'security': '7fcde53eaa50e791', 'limits': '47ba3a75dfec4e36', 'flags': '7d9a68bd350f93da', 'environment': '23c297b65db02704', 'route': '8ea19566ca54e32b'}, 'inventory-sync': {'app': '5e2800c2aea4d249', 'logging': '5e4cc3da4bbbe402', 'db': '48276577aee4e8eb', 'security': 'a00aee2f8fb13d65', 'limits': 'df4befd20e962daf', 'flags': '94a1fc432de3acee', 'environment': 'f2ae34996e6679b8', 'route': '213ba41cb1141b77'}, 'notify-hub': {'app': 'bdacc45b75813faa', 'logging': '524447edba5e95e2', 'db': '0720b6bba7a892ad', 'cache': '4bcf460260235afd', 'security': '8450a05dba767a15', 'limits': 'cdc2db9ce78d0a85', 'environment': 'a7e4f0fa4822eb01', 'route': 'e6bdf45e4adc2127'}, 'report-builder': {'app': 'cafb472c7dc11878', 'logging': '73c0a4ae632b8caa', 'db': '8b5c2c51d5117cf8', 'cache': 'a067886f612f2b5c', 'security': '293ed50250e3072b', 'limits': '866c7969196885cb', 'flags': '42db0f150e1a7646', 'environment': '4237c0e6674cb05f', 'route': 'd6d049b752983c61'}, 'cart-service': {'app': 'a4dec4cc503e9c22', 'logging': '5ccb91d6b0dae5de', 'db': 'fd88fb65cad7ece2', 'cache': '57782bb805224805', 'security': '1f7c550fbe360bb5', 'limits': 'd1c07fb073e24cd2', 'flags': '30a1ad3c76854ac2', 'environment': '1ec8171581254569', 'route': 'aaf7d7230b3368a2'}, 'catalog-index': {'app': '500e81de68227335', 'logging': 'c9eee7b5fb31035d', 'db': '14a9a0bca6dba772', 'security': '4163fd51e4ed185c', 'limits': '1dbf20495bbc7660', 'flags': '682c71e8b12c5778', 'environment': '9c9af3f35880a76f', 'route': 'c41e140f04e709f6'}, 'shipping-bridge': {'app': '9cb1e00cf7441b21', 'logging': '44eaa25fd15d3250', 'db': 'fe0cfa1ca5c0f25f', 'cache': '6cf4ca0b28c2c737', 'security': '4af543f786e70787', 'limits': '34982b5712aefe5a', 'flags': 'f68cea13c2efbadb', 'environment': '5926516ad9de86bb', 'route': '79c1f86b2c91a056'}, 'payments-edge': {'app': '2cddf1e1a24acda1', 'logging': 'd3e4c549338980bb', 'db': 'f2b289ad9faf9e8b', 'cache': 'ca3038d29b3c461f', 'security': '54bf2aa26fa282db', 'limits': '9fb2c7d85c2e07eb', 'flags': 'b29228c525caa946', 'environment': 'be7f70d073658922', 'route': '6d72eae7179f7ab1'}, 'audit-trail': {'app': '2889ba43df54446b', 'logging': 'd1c4be3792fa7e99', 'db': '5a131486046d2f4c', 'cache': '467ade931c4c87ed', 'security': 'a2ca8cde1e164a4a', 'limits': 'bccfbb8d4723b05b', 'flags': '4badf2ddf408939f', 'environment': 'f2895e6df234f38c', 'route': 'dbd8303d8aabb380'}, 'scheduler-core': {'app': '0c97d40aa05fb13b', 'logging': '9d8eb68a2d574b26', 'db': '459a10fa5440a343', 'cache': 'd4b22bc49870bbc4', 'security': 'e444a1508b2444a5', 'limits': 'e9c9a42dcf44e243', 'flags': '42feb45c8fa7310d', 'environment': '4df98a98e72311dc', 'route': '7f4b3a7ca9313d7c'}, 'media-resizer': {'app': 'f5be534a0b568aa8', 'logging': '50e31a44d966ccb8', 'db': '5ae9d29a71ec24f1', 'cache': 'f5bba2ed220babc1', 'security': 'a98f4e766ad96592', 'limits': '84addf886700e21d', 'flags': '7882bf9ee91890f8', 'environment': 'd38c84362ab7956c', 'route': '86301f26e27e71fc'}, 'geo-lookup': {'app': '1aa0b7f966947e1c', 'logging': 'd4e4f841d99e89ed', 'db': '214c9b8ec8d60347', 'cache': '8d662a540f010b2c', 'security': 'd7d487244bf95566', 'limits': '2d04025769d65245', 'flags': '35fa9f8691969f0f', 'environment': '2dac6ebccded800d', 'route': '324429925b8e4c73'}}
V1_DIGESTS = {'orders-api': '324ea8ad3f8c010d', 'billing-worker': '1b8e40e92636fd0e', 'search-gateway': 'a070b8f015c18926', 'auth-proxy': '23b1b0644f0920a6', 'inventory-sync': '1c11c70038420a6b', 'notify-hub': 'cfd2bb36f8c6c2ea', 'report-builder': 'd656b9ae75928d22', 'cart-service': '78f3f6d2aeb7bbc0', 'catalog-index': 'b3503f69982d0c93', 'shipping-bridge': '1c65e3920f88509c', 'payments-edge': '2c6ec7868cdc9270', 'audit-trail': '9ed61307a7b810da', 'scheduler-core': 'd2c8648ccf4cb167', 'media-resizer': '32047c8a3d0f32f9', 'geo-lookup': '1621d0a2699379d9'}


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


problems = []
for service, tables in EXPECTED.items():
    ini = pathlib.Path("configs") / f"{service}.ini"
    if not ini.is_file() or hashlib.sha256(ini.read_text(encoding="utf-8").encode()).hexdigest()[:16] != V1_DIGESTS[service]:
        problems.append(f"{ini}: the v1 file must stay as it was")
    path = pathlib.Path("configs") / f"{service}.toml"
    if not path.is_file():
        problems.append(f"{path} is missing")
        continue
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        problems.append(f"{path} is not valid TOML: {exc}")
        continue
    for table in sorted(set(tables) | set(data)):
        if table not in data:
            problems.append(f"{path}: table [{table}] is missing")
        elif table not in tables:
            problems.append(f"{path}: table [{table}] must not be there")
        elif digest(data[table]) != tables[table]:
            problems.append(f"{path}: table [{table}] differs from the reference")
for problem in problems[:40]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
