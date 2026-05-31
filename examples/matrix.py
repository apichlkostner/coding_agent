import asyncio
import os

from dotenv import load_dotenv
from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteMemberEvent,
    MatrixRoom,
    MegolmEvent,
    RoomMessageText,
)

load_dotenv()

home_server = os.getenv("MATRIX_HOMESERVER_URL", "")
user_id = os.getenv("MATRIX_USER_ID", "")
access_token = os.getenv("MATRIX_ACCESS_TOKEN", "")
device_id = os.getenv("MATRIX_DEVICE_ID", "")
keys_file = os.getenv("MATRIX_KEYS_FILE", "")
keys_passphrase = os.getenv("MATRIX_KEYS_PASSPHRASE", "")


async def main() -> None:
    config = AsyncClientConfig(store_sync_tokens=True)
    client = AsyncClient(
        home_server,
        store_path="./nio_store",
        config=config,
    )

    client.user_id = user_id
    client.access_token = access_token
    client.device_id = device_id
    os.makedirs("./nio_store", exist_ok=True)
    client.load_store()

    # if keys_file:
    #     result = await client.import_keys(keys_file, keys_passphrase)
    #     print(f"Imported keys: {result}")

    # await client.keys_upload()

    response = await client.joined_rooms()
    print("Bot is currently in these rooms:", response.rooms)

    async def message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
        # Skip messages sent by the bot itself
        if event.sender == client.user_id:
            return

        print(f"[{room.display_name}] {event.sender}: {event.body}")

        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={"body": "I can hear you", "msgtype": "m.text"},
            ignore_unverified_devices=True,
        )

    async def invite_callback(room: MatrixRoom, event: InviteMemberEvent) -> None:
        # Auto-accept any invite
        if event.membership == "invite":
            print(f"Accepting invite to room: {room.room_id}")
            await client.join(room.room_id)

    async def decryption_failure_callback(room: MatrixRoom, event: MegolmEvent) -> None:
        print(f"Failed to decrypt message in {room.room_id} from {event.sender}")

    # First sync to skip old messages
    sync_response = await client.sync(timeout=0, full_state=True)

    if sync_response.rooms.invite:
        for room_id in sync_response.rooms.invite:
            print(f"Joining pending invite: {room_id}")
            await client.join(room_id)

    # Register callback AFTER first sync
    client.add_event_callback(message_callback, RoomMessageText)
    client.add_event_callback(invite_callback, InviteMemberEvent)
    client.add_event_callback(decryption_failure_callback, MegolmEvent)

    print(f"Logged in as: {client.user_id}")
    print("Listening for messages...")

    await client.sync_forever(timeout=30000)


asyncio.run(main())
