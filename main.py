import asyncio
import logging
import traceback
import asyncssh
import settings

from asyncssh import SSHClientConnection

logging.basicConfig(level=logging.INFO, filename="routerscript.log", filemode="a")

WRITE = "write"
CHANGE_PASSWORD = "change password"
VERIFY = "verify"

WRITE_OPS = {WRITE, CHANGE_PASSWORD}
VERIFY_OPS = {VERIFY}

TELNET_COMMAND_RULES_INITIAL = [
    (WRITE, "echo 'hello'"),
    (VERIFY, "hello"),
    (CHANGE_PASSWORD, None),
]

TELNET_COMMAND_RULES_FINAL = [
    (WRITE, "echo 'hello'"),
    (VERIFY, "hello"),
]


class Router:
    def __init__(self, address: str, username: str, password: str, port: int = 22):
        self.address = address
        self.username = username
        self.password = password
        self.port = port

    @staticmethod
    def leave_a_trail(info):
        with open("change_password_trail.txt", "a") as f:
            f.write(info + "\n")


async def is_current_line(process, prompt, timeout=10):
    """
    read the terminal output until what the user wants
    ensure user supplied prompt exists in the command line and return result_flag.
    else return empty string
    """
    result_flag = False
    try:
        result = await asyncio.wait_for(
            process.stdout.readuntil(prompt), timeout=timeout
        )
        pwd_prompt = result.split("\n")[-1]
    except Exception:
        logging.error(f"error when waiting for prompt {prompt}")
    else:
        result_flag = True
    return result_flag


async def change_password(conn: SSHClientConnection, new_pwd: str):
    """
    Initiating interactive shell
    defined the list of prompts to be captured when password message prompts.
    creating interactive input for password reset.
    returning successful message once the password reset is completed.
    Password change order
        -> New password:
        -> Retype password:
        -> Password for root changed by root
    """
    successful_prompt = "Password for root changed by root"

    prompts = [
        [
            "New password:",
            new_pwd,
            None,
        ],
        [
            "Retype password:",
            new_pwd,
            None,
        ],
        [successful_prompt, None, None],
    ]

    process = await conn.create_process("passwd root", term_type="xterm")

    for prompt in prompts:
        result_flag = await is_current_line(process, prompt[0])
        if not result_flag:
            return "failed to change password"

        if prompt[0] != successful_prompt:
            process.stdin.write(prompt[1] + "\n")
            logging.info(prompt[2])
    
    return successful_prompt


async def execute_rules(
    conn: SSHClientConnection,
    router: Router,
    rules: list[tuple],
    last_output: str = None,
):
    output = last_output
    try:
        for rule in rules:
            operation, value = rule
            if operation in WRITE_OPS:
                if operation == WRITE:
                    result = await conn.run(value, check=True, timeout=5.0)
                    output = result.stdout
                    logging.info(f"<{router.address}> from service write 1 {value}")
                elif operation == CHANGE_PASSWORD:
                    output = await change_password(conn, router.password)
                    logging.info(
                        f"<{router.address}> from service write 2 mumblejumble"
                    )
            elif operation in VERIFY_OPS:
                if operation == VERIFY:
                    assert value in output
    except Exception:
        raise traceback.format_exc()


async def do_script(router: Router, semaphore):
    async with semaphore:
        retry_flag = False

        try:
            logging.info(f"<{router.address}> establishing connection for the 1st time")
            async with asyncssh.connect(
                host=router.address,
                port=router.port,
                username=router.username,
                password=router.password,
            ) as conn:
                router.password = settings.NEW_ROUTER_PASSWORD
                await execute_rules(
                    conn,
                    router,
                    TELNET_COMMAND_RULES_INITIAL,
                )
            logging.info(f"<{router.address}> 1st time done")

            logging.info(f"<{router.address}> sleeping coroutine for 2 seconds")
            await asyncio.sleep(2)

            logging.info(f"<{router.address}> establishing connection for the 2nd time")
            async with asyncssh.connect(
                host=router.address,
                port=router.port,
                username=router.username,
                password=router.password,
            ) as conn:
                await execute_rules(
                    conn,
                    router,
                    TELNET_COMMAND_RULES_FINAL,
                )
            logging.info(f"<{router.address}> 2nd time done")
        except Exception:
            retry_flag = True
            error = traceback.format_exc()
            logging.error(error)

        if retry_flag:
            info = f"<{router.address}> FAIL : Failed to change password"
        else:
            info = f"<{router.address}> PASS : Successfully changed password"

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
        lines = f.readlines()

    routers = []
    for line in lines:
        if "." in line:
            routers.append(
                Router(
                    line.strip().strip("\n"),
                    settings.ROUTER_USERNAME,
                    settings.ROUTER_PASSWORD,
                )
            )

    for router in routers:
        task = asyncio.create_task(do_script_with_retry(router, semaphore))
        tasks.append(task)

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(job())
    logging.info("Script started")
