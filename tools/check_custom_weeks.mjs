/** Проверяет совместимость календарных недель со старыми строковыми значениями и повторами. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../server_scheduler.mjs', import.meta.url), 'utf8');
const marker = '\nimport fs from "node:fs";';
assert.ok(source.includes(marker), 'Server scheduler core boundary must exist');
const core = source.slice(0, source.indexOf(marker));
const helpers = vm.runInNewContext(`${core}\n({ instanceAppliesToDate, weekNumbersForInstance })`);
const config = { semesterStart: '2026-08-31', semesterEnd: '2026-09-27' };
const instance = { weekPattern: 'custom', customWeeks: ['2', 2, '3', 3, 0, 'bad', 99] };
assert.equal(helpers.instanceAppliesToDate(config, instance, '2026-09-07'), true);
assert.equal(helpers.instanceAppliesToDate(config, instance, '2026-08-31'), false);
assert.deepEqual(Array.from(helpers.weekNumbersForInstance(config, instance)), [2, 3]);
assert.deepEqual(Array.from(helpers.weekNumbersForInstance(config, { ...instance, customWeeks: [] })), []);
console.log('Custom weeks regression checks OK');
