from concurrent.futures import ThreadPoolExecutor, wait, as_completed
import threading
from queue import Queue

from binaryninja.interaction import get_text_line_input, get_choice_input
from binaryninja.mediumlevelil import MediumLevelILInstruction
from binaryninja.enums import MediumLevelILOperation as mlilop
from binaryninja.log import log_info, log_error
from binaryninja import PluginCommand, BinaryView, Function, BackgroundTaskThread, Symbol

def is_call(i: MediumLevelILInstruction) -> bool:
    return i.operation == mlilop.MLIL_CALL

def rename_caller(callee: Function, caller: Function, param_index: int, log_queue: Queue):
    i: MediumLevelILInstruction
    for i in filter(is_call, caller.mlil.instructions):
        if i.operands[1].constant == callee.start:
            i = i.operands[2][param_index]
            if i.operation == mlilop.MLIL_CONST_PTR:
                name: str = caller.view.get_string_at(i.constant).value.split('(', 1)[0]
                caller.view.define_auto_symbol(Symbol(caller.symbol.type, caller.symbol.address, short_name=name))
                log_queue.put(f"Renamed caller: {caller.name}")
                return True
        else:
            log_error(f"Unable to rename {caller.name}")
    return False

def log_worker(log_queue: Queue):
    while True:
        message = log_queue.get()
        if message is None:
            break
        log_info(message)
        log_queue.task_done()

class RenameTask(BackgroundTaskThread):
    def __init__(self, bv: BinaryView, func: Function):
        super().__init__("Renaming callers...", True)
        self.bv = bv
        self.func = func
        self.thread_pool_executor = ThreadPoolExecutor(4)
        self.state = bv.begin_undo_actions()
        self.log_queue = Queue()
        self.log_thread = threading.Thread(target=log_worker, args=(self.log_queue,))
        self.log_thread.start()

    def run(self):
        param_str: str = get_text_line_input("Enter name of parameter", "").decode("utf-8")
        try:
            param = next(p for p in self.func.parameter_vars if p.name == param_str)
            param_index: int = self.func.parameter_vars.vars.index(param)
        except StopIteration:
            log_error(f"arg {param_str} not found")
            return
        callers_length = len(self.func.callers)
        log_info(f"Processing {len(self.func.callers)} callers")
        renamed_count = 0
        futures = [self.thread_pool_executor.submit(rename_caller, self.func, caller, param_index, self.log_queue) for caller in self.func.callers]
        for future in as_completed(futures):
            if future.result():
                renamed_count += 1
            self.progress = f"Renamed {renamed_count}/{callers_length} functions"

        #wait(futures)
        log_info("Renaming callers complete...")

    def cancel(self):
        self.thread_pool_executor.shutdown(True, True)
        undo = get_choice_input("Do you wish to undo any changes made?", "", ["Yes", "No"])
        if undo == "Yes":
            self.bv.revert_undo_actions(self.state)
        else:
            self.bv.commit_undo_actions(self.state)
        self.progress = "Cancelled"

    def finish(self):
        self.bv.commit_undo_actions(self.state)
        self.progress = "Finished"

def rename(bv: BinaryView, func: Function):
    RenameTask(bv, func).start()

PluginCommand.register_for_function(
    "Abuse Log Function",
    "Rename all callers of this function to the specified string argument",
    rename
)