import asyncio
import logging
import traceback
import asyncssh
import settings

from asyncssh import SSHReader, SSHWriter

logging.basicConfig(
    filename="routerscript.log",
    filemode="a",
    format="%(name)s - %(levelname)s - %(message)s",
)

EXPECT = "expect"
WRITE = "write"
READ = "read"
INPUT_PASSWORD_HERE = "input password here"
VERIFY = "verify"

EXPECT_OPS = {EXPECT}
WRITE_OPS = {WRITE, INPUT_PASSWORD_HERE}
READ_OPS = {READ}
VERIFY_OPS = {VERIFY}

TELNET_COMMAND_RULES_INITIAL = [
    (READ, "#"),
    (WRITE, "passwd root"),
    (READ, "New password:"),
    (VERIFY, "Changing password for root"),
    (INPUT_PASSWORD_HERE, "New password:"),
    (READ, "Retype password:"),
    (INPUT_PASSWORD_HERE, "Retype password:"),
    (READ, "#"),
    (VERIFY, "Password for root changed by root"),
]

TELNET_COMMAND_RULES_FINAL = [
    (READ, "#"),
    (VERIFY, "#"),
]


class Router:
    def __init__(self, address, username, password, port=22):
        self.address = address
        self.username = username
        self.password = password
        self.port = port

    def leave_a_trail(info):
        with open("failover_trail.txt", "a") as f:
            f.write(info + "\n")


async def execute_rules(
    reader: SSHReader,
    writer: SSHWriter,
    router: Router,
    rules: list[tuple],
    last_output: str = None,
):
    output = last_output
    try:
        expect = None
        for rule in rules:
            operation, value = rule
            if operation in EXPECT_OPS:
                if operation == EXPECT:
                    expect = value
            elif operation in READ_OPS:
                expect = value
                output = await asyncio.wait_for(reader.read(1024), timeout=10)
                logging.info(f"<{router.address}> from service read {output}")
            elif operation in WRITE_OPS:
                if operation == WRITE:
                    if expect in output:
                        writer.write(value + "\r\n")
                        logging.info(f"<{router.address}> from service write 1 {value}")
                elif operation == INPUT_PASSWORD_HERE:
                    if expect in output:
                        writer.write(router.password + "\r\n")
                        logging.info(
                            f"<{router.address}> from service write 2 mumblejumble"
                        )
            elif operation in VERIFY_OPS:
                if operation == VERIFY:
                    assert value in output

    except AssertionError:
        raise traceback.format_exc()

    except Exception:
        raise traceback.format_exc()


async def do_script(router: Router, semaphore):
    async with semaphore:
        retry_flag = False

        try:
            logging.info(f"<{router.address}> establishing connection for the 1st time")
            conn = await asyncssh.connect(
                host=router.address,
                port=router.port,
                username=router.username,
                password=router.password,
            )
            reader, writer = await conn.open_session()
            router.password = settings.NEW_ROUTER_PASSWORD
            await execute_rules(
                reader,
                writer,
                router,
                TELNET_COMMAND_RULES_INITIAL,
            )
            conn.close()
            await conn.wait_closed()
            logging.info(f"<{router.address}> 1st time done")

            logging.info(f"<{router.address}> sleeping coroutine for 2 seconds")
            await asyncio.sleep(2)

            logging.info(f"<{router.address}> establishing connection for the 2nd time")
            conn = await asyncssh.connect(
                host=router.address,
                port=router.port,
                username=router.username,
                password=router.password,
            )
            reader, writer = await conn.open_session()
            await execute_rules(
                reader,
                writer,
                router,
                TELNET_COMMAND_RULES_FINAL,
            )
            conn.close()
            await conn.wait_closed()
            logging.info(f"<{router.address}> 2nd time done")
            
        except Exception:
            retry_flag = True
            error = traceback.format_exc()
            logging.error(error)

        if retry_flag:
            info = f"<{router.address}> FAIL : Failed to change sim"
        else:
            info = f"<{router.address}> PASS : Failed to get SIM info after failover"

        router.leave_a_trail(info)
        return retry_flag


async def do_script_with_retry(
    router,
    semaphore,
    max_retries=2,
    retry_interval=10,
):
    for retry_count in range(1, max_retries + 1):
        retry_flag = await do_script(router, semaphore)
        within_minimum_retries_flag = retry_count < max_retries

        if within_minimum_retries_flag and retry_flag:
            logging.info(f"<{router.address}> retrying in {retry_interval} seconds...")
            await asyncio.sleep(retry_interval)
        else:
            break


async def job():
    semaphore = asyncio.Semaphore(100)

    tasks = []
    with open("router_ips.txt", "r") as f:
        router_ips = f.readlines()

    routers = []
    for router_ip in router_ips:
        routers.append(
            Router(
                router_ip.strip().strip("\n"),
                settings.ROUTER_USERNAME,
                settings.ROUTER_PASSWORD,
            )
        )

    for router in routers:
        task = asyncio.create_task(do_script_with_retry(router, semaphore))
        tasks.append(task)

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    logging.info("Starting script")
    asyncio.run(job())
    logging.info("Script started")
