import { TestBed } from '@angular/core/testing';
import { Tag } from './tag';

describe('Tag', () => {
  it('hiển thị giá trị và class theo mức độ', async () => {
    await TestBed.configureTestingModule({
      imports: [Tag],
    }).compileComponents();
    const fixture = TestBed.createComponent(Tag);
    fixture.componentRef.setInput('value', 'đọc');
    fixture.componentRef.setInput('severity', 'warn');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent?.trim()).toBe('đọc');
    expect(el.classList).toContain('sev-warn');
  });
});
