from selfdrive.car.hyundai.mdps_fault_log import MdpsFaultLog, POST_FRAMES, PRE_FRAMES


def row(i):
  return (1794000000.0 + i * 0.01, 10.0, 80.0, float(i % 50), 1.0, 0.0, 0.5, 100, True, False)


def run(log, faults):
  for i in range(600):
    log.update((0, 1 if i in faults else 0, 0), row(i))


def test_records_two_seconds_before_and_one_after_each_fault():
  log = MdpsFaultLog(start_thread=False)
  run(log, set(range(300, 320)))
  count, block, trigger = log.out.get_nowait()
  assert count == 1
  assert len(block) == PRE_FRAMES + 1 + POST_FRAMES
  assert block[PRE_FRAMES][-1] == (0, 1, 0)      # the first fault frame
  assert not any(block[PRE_FRAMES - 1][-1])
  text = log.text(count, block, trigger)
  assert text.startswith("== MDPS fault #1 at ")
  assert trigger is block[PRE_FRAMES]
  assert len(text.splitlines()) == 2 + len(block)


def test_no_write_without_a_fault_and_one_block_per_fault_edge():
  log = MdpsFaultLog(start_thread=False)
  run(log, set())
  assert log.out.empty()
  log = MdpsFaultLog(start_thread=False)
  run(log, {100, 101, 450})
  assert log.out.qsize() == 2


def test_header_names_the_frame_that_started_this_record():
  log = MdpsFaultLog(start_thread=False)
  run(log, {100, 101, 250})
  log.out.get_nowait()
  count, block, trigger = log.out.get_nowait()
  assert count == 2
  assert trigger == block[PRE_FRAMES]
  assert trigger[0] == row(250)[0]
