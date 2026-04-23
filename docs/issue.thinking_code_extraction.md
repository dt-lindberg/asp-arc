- [x] Issue 1: Thinking goes on for too long, thinks for ~80K tokens and then response is empty. 
- - Resolved by increasing max tokens and max mode length.
- [ ] Issue 2: Model sometimes responds with <asp>...</asp> code in response, but other times ```asp ...```, our code-extraction should support both. Even if both are present it should work. Have to make the extraction stronger.
- [ ] Issue 3: Looking at the output logs (src/outputs/slurm_output_22107501.out), very often, the first time a program is passed to Clingo for verification, it is empty / has 0 characters, but the second program that gets passed for verification has non-zero characters. Not sure what's causing this bug, included detailed logs at the bottom.



---
logs from slurm_output_22107501.out
```bash
(base) [dlindberg@int6 outputs]$ rg -A 4 "verifying refined program on Clingo..." slurm_output_22107501.out
151:2026-04-22 08:45:33,562 [DEBUG] __main__ - _run:103 >   [8d510a79] verifying refined program on Clingo...
152-2026-04-22 08:45:33,562 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (1611 chars)
153-2026-04-22 08:45:33,567 [DEBUG] utils.clingo - run_clingo:74 > Clingo grounding error: grounding stopped because of errors
154-2026-04-22 08:45:33,570 [DEBUG] __main__ - _run:112 >   [8d510a79] attempt 1: 0/2 correct
155:2026-04-22 08:45:33,570 [DEBUG] __main__ - _run:103 >   [39e1d7f9] verifying refined program on Clingo...
156-2026-04-22 08:45:33,570 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
157-2026-04-22 08:45:33,571 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
158-2026-04-22 08:45:33,571 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (8737 chars)
159-2026-04-22 08:45:33,576 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
168:2026-04-22 08:45:33,597 [DEBUG] __main__ - _run:103 >   [8a004b2b] verifying refined program on Clingo...
169-2026-04-22 08:45:33,597 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
170-2026-04-22 08:45:33,597 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
171-2026-04-22 08:45:33,598 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (4285 chars)
172-2026-04-22 08:45:33,600 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
186:2026-04-22 08:54:56,625 [DEBUG] __main__ - _run:103 >   [8d510a79] verifying refined program on Clingo...
187-2026-04-22 08:54:56,625 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (1830 chars)
188-2026-04-22 08:54:56,628 [DEBUG] utils.clingo - run_clingo:74 > Clingo grounding error: grounding stopped because of errors
189-2026-04-22 08:54:56,632 [DEBUG] __main__ - _run:112 >   [8d510a79] attempt 2: 0/2 correct
190:2026-04-22 08:54:56,633 [DEBUG] __main__ - _run:103 >   [39e1d7f9] verifying refined program on Clingo...
191-2026-04-22 08:54:56,633 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (736 chars)
192-2026-04-22 08:54:56,633 [DEBUG] utils.clingo - run_clingo:48 > Clingo parse error: parsing failed (6 messages)
193-2026-04-22 08:54:56,642 [DEBUG] __main__ - _run:112 >   [39e1d7f9] attempt 2: 0/3 correct
194:2026-04-22 08:54:56,642 [DEBUG] __main__ - _run:103 >   [8a004b2b] verifying refined program on Clingo...
195-2026-04-22 08:54:56,642 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
196-2026-04-22 08:54:56,643 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
197-2026-04-22 08:54:56,643 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (4285 chars)
198-2026-04-22 08:54:56,645 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
212:2026-04-22 09:05:17,554 [DEBUG] __main__ - _run:103 >   [8d510a79] verifying refined program on Clingo...
213-2026-04-22 09:05:17,554 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (1473 chars)
214-2026-04-22 09:05:17,555 [DEBUG] utils.clingo - run_clingo:48 > Clingo parse error: parsing failed (15 messages)
215-2026-04-22 09:05:17,558 [DEBUG] __main__ - _run:112 >   [8d510a79] attempt 3: 0/2 correct
216:2026-04-22 09:05:17,558 [DEBUG] __main__ - _run:103 >   [39e1d7f9] verifying refined program on Clingo...
217-2026-04-22 09:05:17,559 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
218-2026-04-22 09:05:17,559 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
219-2026-04-22 09:05:17,560 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (8737 chars)
220-2026-04-22 09:05:17,565 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
229:2026-04-22 09:05:17,591 [DEBUG] __main__ - _run:103 >   [8a004b2b] verifying refined program on Clingo...
230-2026-04-22 09:05:17,591 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
231-2026-04-22 09:05:17,592 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
232-2026-04-22 09:05:17,593 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (4285 chars)
233-2026-04-22 09:05:17,595 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
247:2026-04-22 09:14:27,390 [DEBUG] __main__ - _run:103 >   [8d510a79] verifying refined program on Clingo...
248-2026-04-22 09:14:27,390 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (1346 chars)
249-2026-04-22 09:14:27,395 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
250-2026-04-22 09:14:27,396 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (2747 chars)
251-2026-04-22 09:14:27,398 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 0 answer set(s)
--
257:2026-04-22 09:14:27,412 [DEBUG] __main__ - _run:103 >   [39e1d7f9] verifying refined program on Clingo...
258-2026-04-22 09:14:27,413 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (1741 chars)
259-2026-04-22 09:14:27,413 [DEBUG] utils.clingo - run_clingo:48 > Clingo parse error: parsing failed (10 messages)
260-2026-04-22 09:14:27,425 [DEBUG] __main__ - _run:112 >   [39e1d7f9] attempt 4: 0/3 correct
261:2026-04-22 09:14:27,425 [DEBUG] __main__ - _run:103 >   [8a004b2b] verifying refined program on Clingo...
262-2026-04-22 09:14:27,425 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
263-2026-04-22 09:14:27,426 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
264-2026-04-22 09:14:27,426 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (4285 chars)
265-2026-04-22 09:14:27,429 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
279:2026-04-22 09:25:54,134 [DEBUG] __main__ - _run:103 >   [8d510a79] verifying refined program on Clingo...
280-2026-04-22 09:25:54,135 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
281-2026-04-22 09:25:54,136 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
282-2026-04-22 09:25:54,136 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (1401 chars)
283-2026-04-22 09:25:54,138 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
289:2026-04-22 09:25:54,145 [DEBUG] __main__ - _run:103 >   [39e1d7f9] verifying refined program on Clingo...
290-2026-04-22 09:25:54,145 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
291-2026-04-22 09:25:54,146 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
292-2026-04-22 09:25:54,146 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (8737 chars)
293-2026-04-22 09:25:54,151 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
--
302:2026-04-22 09:25:54,179 [DEBUG] __main__ - _run:103 >   [8a004b2b] verifying refined program on Clingo...
303-2026-04-22 09:25:54,179 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (0 chars)
304-2026-04-22 09:25:54,180 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
305-2026-04-22 09:25:54,180 [DEBUG] utils.clingo - run_clingo:45 > Adding program to Clingo (4285 chars)
306-2026-04-22 09:25:54,182 [DEBUG] utils.clingo - run_clingo:89 > Clingo: 1 answer set(s)
```

