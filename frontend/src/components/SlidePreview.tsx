import React from 'react';
import type {
  CellEl,
  ChartEl,
  GroupEl,
  ImageEl,
  Para,
  ShapeEl,
  SlideElement,
  SlideModel,
  SvgEl,
  TableEl,
  TextEl,
} from '../types';
import { hex, hexAlpha, makeScale, type Scale } from '../lib/units';

interface SlidePreviewProps {
  slide: SlideModel;
  deckWidth: number;
  deckHeight: number;
  className?: string;
  onClick?: () => void;
  showIndexBadge?: boolean;
}

export const SlidePreview: React.FC<SlidePreviewProps> = ({
  slide,
  deckWidth,
  deckHeight,
  className = '',
  onClick,
  showIndexBadge = false,
}) => {
  const bg = hex(slide.background, '#ffffff');
  const scale = React.useMemo(() => makeScale(deckWidth), [deckWidth]);
  const aspectRatio = `${deckWidth} / ${deckHeight}`;

  return (
    <div
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onClick();
        }
      }}
      className={`slide-frame group relative w-full overflow-hidden select-none transition-shadow ${
        onClick ? 'cursor-pointer focus-visible:ring-2 focus-visible:ring-[var(--color-proof)]' : ''
      } ${className}`}
      style={{
        backgroundColor: bg,
        aspectRatio,
      }}
    >
      {slide.elements.map((el, i) => (
        <RenderElement key={i} el={el} scale={scale} deckWidth={deckWidth} deckHeight={deckHeight} />
      ))}

      {showIndexBadge && (
        <div className="absolute bottom-1 right-2 pointer-events-none text-[2.2cqw] font-mono font-medium opacity-60 text-slate-500">
          {slide.number || 1}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------- Element Dispatcher

const RenderElement: React.FC<{
  el: SlideElement;
  scale: Scale;
  deckWidth: number;
  deckHeight: number;
}> = ({ el, scale, deckWidth, deckHeight }) => {
  switch (el.kind) {
    case 'text':
      return <RenderText el={el} scale={scale} />;
    case 'shape':
      return <RenderShape el={el} scale={scale} />;
    case 'table':
      return <RenderTable el={el} scale={scale} />;
    case 'chart':
      return <RenderChart el={el} scale={scale} />;
    case 'image':
      return <RenderImage el={el} scale={scale} />;
    case 'svg':
      return <RenderSvg el={el} scale={scale} />;
    case 'group':
      return <RenderGroup el={el} scale={scale} deckWidth={deckWidth} deckHeight={deckHeight} />;
    default: {
      const _exhaustive: never = el;
      return _exhaustive;
    }
  }
};

// ---------------------------------------------------------------- Text Element

const RenderText: React.FC<{ el: TextEl; scale: Scale }> = ({ el, scale }) => {
  const vAlignMap = {
    top: 'flex-start',
    middle: 'center',
    bottom: 'flex-end',
  } as const;

  return (
    <div
      style={{
        position: 'absolute',
        left: scale.emu(el.x),
        top: scale.emu(el.y),
        width: scale.emu(el.w),
        height: scale.emu(el.h),
        backgroundColor: el.fill ? hex(el.fill) : 'transparent',
        padding: el.inset > 0 ? scale.emu(el.inset) : '0.15cqw',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: vAlignMap[el.valign] ?? 'flex-start',
        overflow: 'hidden',
        boxSizing: 'border-box',
        whiteSpace: el.wrap ? 'normal' : 'nowrap',
      }}
    >
      {el.paras.map((p, idx) => (
        <RenderPara key={idx} p={p} scale={scale} />
      ))}
    </div>
  );
};

const RenderPara: React.FC<{ p: Para; scale: Scale }> = ({ p, scale }) => {
  const isNumbered = Boolean(p.bullet && /^\d+\.?$/.test(p.bullet));

  return (
    <div
      style={{
        textAlign: p.align ?? 'left',
        paddingLeft: p.level ? `${p.level * 2.8}cqw` : undefined,
        marginTop: p.space_before ? scale.pt(p.space_before) : undefined,
        marginBottom: p.space_after ? scale.pt(p.space_after) : undefined,
        lineHeight: p.line_spacing && p.line_spacing > 1.0 ? p.line_spacing : 1.2,
        display: 'flex',
        alignItems: 'baseline',
      }}
    >
      {p.bullet && (
        <span
          style={{
            marginRight: '0.5cqw',
            color: p.bullet_color ? hex(p.bullet_color) : 'inherit',
            opacity: 0.9,
            fontSize: isNumbered ? '0.9em' : '1em',
            flexShrink: 0,
          }}
        >
          {p.bullet}
        </span>
      )}

      <div style={{ flex: 1, minWidth: 0, overflowWrap: 'break-word' }}>
        {p.runs.map((r, ri) => {
          const textDecs: string[] = [];
          if (r.underline) textDecs.push('underline');
          if (r.strike) textDecs.push('line-through');

          return (
            <span
              key={ri}
              style={{
                fontFamily: r.font ? `"${r.font}", system-ui, sans-serif` : 'inherit',
                fontSize: scale.pt(r.size),
                fontWeight: r.bold ? 700 : 400,
                fontStyle: r.italic ? 'italic' : 'normal',
                textDecoration: textDecs.length ? textDecs.join(' ') : 'none',
                verticalAlign: r.sup ? 'super' : r.sub ? 'sub' : 'baseline',
                color: r.color ? hex(r.color) : 'inherit',
              }}
            >
              {r.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- Shape Element

const RenderShape: React.FC<{ el: ShapeEl; scale: Scale }> = ({ el, scale }) => {
  let borderRadius = '0';
  let clipPath: string | undefined;

  if (el.geom === 'roundRect') {
    const rad = el.radius > 0 ? Math.min(el.radius, 0.5) : 0.04;
    borderRadius = `${rad * 32}cqw`;
  } else if (el.geom === 'ellipse') {
    borderRadius = '9999px';
  } else if (el.geom === 'homePlate') {
    clipPath = 'polygon(0% 0%, 82% 0%, 100% 50%, 82% 100%, 0% 100%)';
  } else if (el.geom === 'chevron') {
    clipPath = 'polygon(0% 0%, 80% 0%, 100% 50%, 80% 100%, 0% 100%, 20% 50%)';
  } else if (el.geom === 'triangle') {
    clipPath = 'polygon(50% 0%, 0% 100%, 100% 100%)';
  }

  let background = 'transparent';
  if (el.gradient && el.gradient.length > 1) {
    background = `linear-gradient(${el.gradient_angle ?? 90}deg, ${el.gradient.map((c) => hex(c)).join(', ')})`;
  } else if (el.fill) {
    background = hexAlpha(el.fill, el.fill_alpha ?? 1);
  }

  const border =
    el.line && el.line_w > 0
      ? `${scale.pt(el.line_w)} ${el.dash ? 'dashed' : 'solid'} ${hex(el.line)}`
      : 'none';

  const vAlignMap = {
    top: 'flex-start',
    middle: 'center',
    bottom: 'flex-end',
  } as const;

  return (
    <div
      style={{
        position: 'absolute',
        left: scale.emu(el.x),
        top: scale.emu(el.y),
        width: scale.emu(el.w),
        height: scale.emu(el.h),
        backgroundColor: background.startsWith('linear') ? undefined : background,
        backgroundImage: background.startsWith('linear') ? background : undefined,
        border,
        borderRadius,
        clipPath,
        boxShadow: el.shadow ? '0 0.4cqw 1cqw rgba(0,0,0,0.18)' : undefined,
        display: el.paras && el.paras.length > 0 ? 'flex' : 'block',
        flexDirection: 'column',
        justifyContent: vAlignMap[el.valign] ?? 'center',
        padding: el.inset > 0 ? scale.emu(el.inset) : '0.2cqw',
        overflow: 'hidden',
        boxSizing: 'border-box',
      }}
    >
      {el.paras?.map((p, idx) => (
        <RenderPara key={idx} p={p} scale={scale} />
      ))}
    </div>
  );
};

// ---------------------------------------------------------------- Table Element

const RenderTable: React.FC<{ el: TableEl; scale: Scale }> = ({ el, scale }) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: scale.emu(el.x),
        top: scale.emu(el.y),
        width: scale.emu(el.w),
        height: scale.emu(el.h),
        overflow: 'hidden',
        boxSizing: 'border-box',
      }}
    >
      <table
        style={{
          width: '100%',
          height: '100%',
          borderCollapse: 'collapse',
          tableLayout: 'fixed',
        }}
      >
        <tbody>
          {el.rows.map((row, rIdx) => (
            <tr key={rIdx}>
              {row.map((cell, cIdx) => (
                <RenderCell key={cIdx} cell={cell} scale={scale} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const RenderCell: React.FC<{ cell: CellEl; scale: Scale }> = ({ cell, scale }) => {
  if (cell.merged) return null;

  return (
    <td
      colSpan={cell.colspan > 1 ? cell.colspan : undefined}
      rowSpan={cell.rowspan > 1 ? cell.rowspan : undefined}
      style={{
        backgroundColor: cell.fill ? hex(cell.fill) : 'transparent',
        borderTop:
          cell.border_top && cell.border_top_w > 0
            ? `${scale.pt(cell.border_top_w)} solid ${hex(cell.border_top)}`
            : undefined,
        borderBottom:
          cell.border_bottom && cell.border_bottom_w > 0
            ? `${scale.pt(cell.border_bottom_w)} solid ${hex(cell.border_bottom)}`
            : '0.08cqw solid rgba(120,120,120,0.2)',
        padding: '0.3cqw 0.6cqw',
        verticalAlign: cell.valign === 'top' ? 'top' : cell.valign === 'bottom' ? 'bottom' : 'middle',
      }}
    >
      {cell.paras.map((p, idx) => (
        <RenderPara key={idx} p={p} scale={scale} />
      ))}
    </td>
  );
};

// ---------------------------------------------------------------- Chart Element

const RenderChart: React.FC<{ el: ChartEl; scale: Scale }> = ({ el, scale }) => {
  const spec = el.spec;
  const cats = spec.categories || [];
  const series = spec.series || [];
  const palette = el.palette && el.palette.length > 0 ? el.palette : ['1D4ED8', '0891B2', '10B981', 'F59E0B'];

  // Calculate maximum value for normalization
  const allValues = series.flatMap((s) => s.values.filter((v): v is number => v !== null && !isNaN(v)));
  const maxVal = Math.max(...allValues, 1);

  return (
    <div
      style={{
        position: 'absolute',
        left: scale.emu(el.x),
        top: scale.emu(el.y),
        width: scale.emu(el.w),
        height: scale.emu(el.h),
        display: 'flex',
        flexDirection: 'column',
        padding: '0.6cqw',
        backgroundColor: 'rgba(240, 243, 246, 0.45)',
        border: '0.08cqw solid rgba(180, 185, 195, 0.4)',
        borderRadius: '0.5cqw',
        boxSizing: 'border-box',
        overflow: 'hidden',
      }}
    >
      {spec.title && (
        <div
          style={{
            fontFamily: el.font ? `"${el.font}", sans-serif` : 'inherit',
            fontSize: scale.pt(el.font_size ? Math.max(el.font_size * 0.9, 10) : 11),
            fontWeight: 600,
            color: hex(el.text_color, '#1a1d23'),
            textAlign: 'center',
            marginBottom: '0.4cqw',
          }}
        >
          {spec.title}
        </div>
      )}

      {/* Chart Canvas Graphic */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'space-around',
          gap: '0.6cqw',
          borderBottom: `0.08cqw solid ${hex(el.grid_color, '#cbd5e1')}`,
          paddingBottom: '0.3cqw',
          minHeight: 0,
        }}
      >
        {cats.map((cat, cIdx) => (
          <div
            key={cIdx}
            style={{
              flex: 1,
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'flex-end',
              alignItems: 'center',
              minWidth: 0,
            }}
          >
            {/* Bars for this category */}
            <div
              style={{
                width: '100%',
                height: '84%',
                display: 'flex',
                alignItems: 'flex-end',
                justifyContent: 'center',
                gap: '0.15cqw',
              }}
            >
              {series.map((s, sIdx) => {
                const val = s.values[cIdx] ?? 0;
                const pct = Math.max((val / maxVal) * 100, 6);
                const color = hex(palette[sIdx % palette.length]);

                return (
                  <div
                    key={sIdx}
                    title={`${s.name}: ${val}`}
                    style={{
                      flex: 1,
                      height: `${pct}%`,
                      backgroundColor: color,
                      borderRadius: '0.2cqw 0.2cqw 0 0',
                      minWidth: '0.2cqw',
                      transition: 'height 0.2s',
                    }}
                  />
                );
              })}
            </div>
            <span
              style={{
                fontSize: '1.2cqw',
                color: hex(el.text_color, '#64748b'),
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                maxWidth: '100%',
                marginTop: '0.2cqw',
              }}
            >
              {cat}
            </span>
          </div>
        ))}
      </div>

      {/* Legend */}
      {el.legend && series.length > 1 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: '1.2cqw',
            marginTop: '0.4cqw',
            fontSize: '1.2cqw',
          }}
        >
          {series.map((s, idx) => (
            <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '0.3cqw' }}>
              <div
                style={{
                  width: '0.8cqw',
                  height: '0.8cqw',
                  borderRadius: '0.15cqw',
                  backgroundColor: hex(palette[idx % palette.length]),
                }}
              />
              <span style={{ color: hex(el.text_color, '#475569') }}>{s.name || `Series ${idx + 1}`}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------- Image Element

const RenderImage: React.FC<{ el: ImageEl; scale: Scale }> = ({ el, scale }) => {
  return (
    <img
      src={`data:${el.mime || 'image/png'};base64,${el.b64}`}
      alt={el.name || 'Slide image'}
      style={{
        position: 'absolute',
        left: scale.emu(el.x),
        top: scale.emu(el.y),
        width: scale.emu(el.w),
        height: scale.emu(el.h),
        objectFit: 'contain',
      }}
    />
  );
};

// ---------------------------------------------------------------- SVG Element

const RenderSvg: React.FC<{ el: SvgEl; scale: Scale }> = ({ el, scale }) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: scale.emu(el.x),
        top: scale.emu(el.y),
        width: scale.emu(el.w),
        height: scale.emu(el.h),
        color: el.recolor ? hex(el.recolor) : undefined,
      }}
      dangerouslySetInnerHTML={{ __html: el.svg }}
    />
  );
};

// ---------------------------------------------------------------- Group Element

const RenderGroup: React.FC<{
  el: GroupEl;
  scale: Scale;
  deckWidth: number;
  deckHeight: number;
}> = ({ el, scale, deckWidth, deckHeight }) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: scale.emu(el.x),
        top: scale.emu(el.y),
        width: scale.emu(el.w),
        height: scale.emu(el.h),
      }}
    >
      {el.children.map((child, idx) => (
        <RenderElement key={idx} el={child} scale={scale} deckWidth={deckWidth} deckHeight={deckHeight} />
      ))}
    </div>
  );
};
