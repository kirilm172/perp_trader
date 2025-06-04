import asyncio

from settings import console


class BaseModule:
    name = None

    def get_tasks(self):
        raise NotImplementedError('get_tasks method must be implemented')

    def log_starting_message(self, name):
        console.log(f'[blue]Starting {name} work...[/blue]')

    def log_finished_message(self, name):
        console.log(f'[blue]{name} work finished.[/blue]')

    def log_error_message(self, name, error):
        console.log(f'[red]Error in {name} work: {error}[/red]')
        console.print_exception(error)

    def log_cancelled_message(self, name):
        console.log(f'[blue]{name} work cancelled.[/blue]')

    async def work(self):
        self.log_starting_message(self.name)
        try:
            async with asyncio.TaskGroup() as group:
                for task in self.get_tasks():
                    group.create_task(task)
        except asyncio.CancelledError:
            self.log_cancelled_message(self.name)
        except Exception as e:
            self.log_error_message(self.name, e)
        self.log_finished_message(self.name)
