import { describe, expect, it } from 'vitest'
import { OWNED_DOMAINS, FORBIDDEN_DOMAINS, PROJECT_ID } from './ownership.js'

describe('Operations frontend boundary', () => {
  it('contains Operations domains and excludes Marketing domains', () => {
    expect(PROJECT_ID).toBe('teleautomation-operations')
    expect(OWNED_DOMAINS).toContain('candidates')
    expect(OWNED_DOMAINS.filter(item => FORBIDDEN_DOMAINS.includes(item))).toEqual([])
  })
})
