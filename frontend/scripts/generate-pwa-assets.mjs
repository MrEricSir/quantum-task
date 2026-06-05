/**
 * Generates Apple splash screen PNGs for common iPhone sizes.
 * Run from the frontend directory: node scripts/generate-pwa-assets.mjs
 *
 * Background: #07040f (app theme colour)
 * Icon: public/icon.svg, centred at ~30% of the shorter dimension
 */
import sharp from 'sharp'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dir = dirname(fileURLToPath(import.meta.url))
const ROOT  = resolve(__dir, '..')
const OUT   = resolve(ROOT, 'public/splash')

const BG = { r: 7, g: 4, b: 15, alpha: 1 }   // #07040f

// [label, width, height] — portrait physical pixels
// Logical (CSS) sizes are what iOS matches in media queries; physical = logical × ratio.
const SIZES = [
  ['iphone-se',        750,  1334],  // SE 2nd/3rd  375×667  @2x
  ['iphone-12mini',   1125,  2436],  // 12/13 mini  375×812  @3x
  ['iphone-12',       1170,  2532],  // 12/13/14/15 390×844  @3x
  ['iphone-14plus',   1284,  2778],  // 14/15 Plus  428×926  @3x
  ['iphone-14pro',    1179,  2556],  // 14/15 Pro   393×852  @3x
  ['iphone-14promax', 1290,  2796],  // 14/15 Pro Max 430×932 @3x
]

const iconSvg = readFileSync(resolve(ROOT, 'public/icon.svg'))

async function makeSplash(label, w, h) {
  const iconSize = Math.round(Math.min(w, h) * 0.30)
  const iconPng  = await sharp(iconSvg).resize(iconSize, iconSize).png().toBuffer()
  const left = Math.round((w - iconSize) / 2)
  const top  = Math.round((h - iconSize) / 2)

  await sharp({ create: { width: w, height: h, channels: 4, background: BG } })
    .composite([{ input: iconPng, left, top }])
    .png({ compressionLevel: 9 })
    .toFile(`${OUT}/${label}.png`)

  console.log(`  ${label}.png  (${w}x${h})`)
}

console.log('Generating splash screens...')
for (const [label, w, h] of SIZES) {
  await makeSplash(label, w, h)
}
console.log('Done.')
