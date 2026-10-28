import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { type CitationDisplayEntry, CitationList } from './CitationList';
import {
  localManualCitation,
  nativeDocumentCitation
} from './documentCitation';

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));
vi.mock('../../states/EvidenceViewerState', () => ({
  useEvidenceViewerState: (select: (state: { open: () => void }) => unknown) =>
    select({ open: vi.fn() })
}));

const source: CitationDisplayEntry = {
  ordinal: 1,
  available: true,
  sourceType: 'related_attached_manual',
  sourceId: '68',
  sourceRevision:
    'c67a39375424ab0135e5f95e504f8e0de6d8d78e8929c2ba6cd809023ac59df7',
  asOf: '2026-10-27T23:24:58Z',
  locator: {
    page: null,
    field:
      '/media/attachments/assetmachine/102/S_Line_horizontal_brushless_synchronous_motor_IOM__c67a39375424.pdf#page=25'
  }
};

describe('native document citation navigation', () => {
  it('keeps the actual T03 attachment/revision and physical page identity', () => {
    expect(nativeDocumentCitation(source)).toEqual({
      attachmentId: 68,
      modelType: 'assetmachine',
      modelId: 102,
      revision: source.sourceRevision,
      pageIndex: 24
    });
  });

  it('requires available authorized source kind, numeric id and exact revision', () => {
    for (const change of [
      { available: false },
      { sourceType: 'model_prose' },
      { sourceId: 'ATT-68' },
      { sourceId: '9007199254740993' },
      { sourceRevision: undefined },
      { sourceRevision: 'latest' }
    ])
      expect(nativeDocumentCitation({ ...source, ...change })).toBeNull();
  });

  it('rejects external, arbitrary, unsafe and non-PDF locators', () => {
    for (const field of [
      'javascript:alert(1)',
      '//evil.test/manual.pdf#page=25',
      'https://evil.test/media/attachments/assetmachine/102/manual.pdf#page=25',
      '/web/machines/machine/102/#page=25',
      '/media/attachments/assetmachine/102/manual.html#page=25',
      '/media/attachments/assetmachine/102/manual.pdf?token=secret#page=25',
      '/media/attachments/assetmachine/102/a%2fb.pdf#page=25',
      '/media/attachments/assetmachine/102/a%5cb.pdf#page=25',
      '/media/attachments/assetmachine/102/a%00b.pdf#page=25',
      '/media/attachments/assetmachine/102/manual.pdf#page=0',
      '/media/attachments/assetmachine/102/manual.pdf#page=1.5',
      '/media/attachments/assetmachine/102/manual.pdf#page=10001',
      '/media/attachments/assetmachine/102/manual.pdf#page=25&x=1',
      '/media/attachments/repairpacket/35/manual.pdf#page=25'
    ])
      expect(
        nativeDocumentCitation({ ...source, locator: { field } })
      ).toBeNull();
  });

  it('rejects mismatched locator coordinates without clamping or substituting', () => {
    expect(
      nativeDocumentCitation({
        ...source,
        locator: { ...source.locator, page: 24 }
      })
    ).toBeNull();
  });

  it('keeps an unsupported packet copy as factual metadata, not a fake viewer target', () => {
    const packet = {
      ...source,
      sourceId: '204',
      locator: {
        field: '/media/attachments/repairpacket/35/WEG-reference.pdf#page=25'
      }
    };
    expect(localManualCitation(packet)?.pageIndex).toBe(24);
    expect(nativeDocumentCitation(packet)).toBeNull();
  });

  it('renders a real named keyboard button only for a supported authorized citation', () => {
    const render = (entry: CitationDisplayEntry) =>
      renderToStaticMarkup(
        createElement(
          MantineProvider,
          {},
          createElement(CitationList, { citations: [entry] })
        )
      );
    const html = render({ ...source, sourceTitle: source.locator!.field! });
    expect(html).toContain('<button');
    expect(html).toContain('type="button"');
    expect(html).toContain('aria-label="Open related manual 68, page 25"');
    expect(html).toContain('Open cited PDF · p. 25');
    expect(html).not.toContain('href=');
    expect(html).not.toContain('/media/attachments/');
    expect(html.match(new RegExp(source.sourceRevision!, 'g'))).toHaveLength(1);
    expect(render({ ...source, available: false })).not.toContain('<button');
    expect(render({ ...source, sourceType: 'model_prose' })).not.toContain(
      '<button'
    );
  });
});
