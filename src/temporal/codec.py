import zlib
from typing import Sequence

from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

ENCODING_ZLIB = b"binary/zlib"


class ZlibCodec(PayloadCodec):
    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        return [
            Payload(
                metadata={"encoding": ENCODING_ZLIB},
                data=zlib.compress(p.SerializeToString()),
            )
            for p in payloads
        ]

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        return [
            Payload.FromString(zlib.decompress(p.data))
            if (p.metadata.get("encoding") == ENCODING_ZLIB)
            else p
            for p in payloads
        ]
