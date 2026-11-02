"""Call one discovered owned ok-script MCP server without exposing its bearer token."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from benchmark_ocr import ROOT


async def main():
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from ok.core.mcp_server import discover_mcp_servers

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool')
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--arguments', type=Path, help='JSON object file')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    servers = discover_mcp_servers(ROOT, artifact_directory=args.artifacts.resolve())
    if len(servers) != 1:
        raise RuntimeError(f'Expected one owned server in the selected directory, found {len(servers)}')
    server = servers[0]
    if Path(server['project_directory']).resolve() != ROOT:
        raise RuntimeError('Server project does not match this workspace')
    arguments = json.loads(args.arguments.read_text(encoding='utf-8')) if args.arguments else {}
    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + server['token']},
                                  trust_env=False, timeout=45) as client:
        async with streamable_http_client(server['url'], http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = (await session.call_tool(args.tool, arguments)).model_dump(mode='json')
                with args.output.open('x', encoding='utf-8') as target:
                    json.dump(result, target, ensure_ascii=False, indent=2)
                print(json.dumps(result.get('structured_content') or result['content'], ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
