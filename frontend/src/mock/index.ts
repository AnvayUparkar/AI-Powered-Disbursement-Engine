import type { Case } from '@/types';
import { case1 } from './case1';
import { case2 } from './case2';
import { case3 } from './case3';
import { case4 } from './case4';
import { case5 } from './case5';
import { case6 } from './case6';

export const cases: Case[] = [case1, case2, case3, case4, case5, case6];

export * from './documents';
export * from './review';
export * from './audit';
export * from './reports';
export * from './helpers';
