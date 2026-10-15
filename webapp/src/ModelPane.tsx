import { Edges, Grid, OrbitControls } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useState } from 'react'
import type { Scene } from 'three'
import type { FurnitureModule, PanelSolid } from './types'
import { materialColors } from './types'

interface Props {
  module: FurnitureModule
  solids: PanelSolid[]
  hoveredPartIds: string[]
  reviewPartIds: string[]
  onHover: (partIds: string[]) => void
  onSelect: (solid: PanelSolid) => void
  onScene: (scene: Scene) => void
}
type View = 'iso' | 'front' | 'top' | 'side'
export default function ModelPane({
  module,
  solids,
  hoveredPartIds,
  reviewPartIds,
  onHover,
  onSelect,
  onScene,
}: Props) {
  const [edges, setEdges] = useState(false)
  const [open, setOpen] = useState(false)
  const [highlightReview, setHighlightReview] = useState(true)
  const [view, setView] = useState<View>('iso')
  const max = Math.max(module.width_mm, module.height_mm, module.depth_mm) * 0.001
  return (
    <section className="model-panel">
      <div className="pane-toolbar">
        <div>
          <strong>3D Model</strong>
          <span className="pane-subtitle">{solids.length} parts · mm</span>
        </div>
        <div className="model-tools">
          <button
            onClick={() => setHighlightReview((value) => !value)}
            className={highlightReview ? 'tool-active' : ''}
          >
            Highlight uncertain
          </button>
          <button onClick={() => setEdges((value) => !value)} className={edges ? 'tool-active' : ''}>
            Edges
          </button>
          <button onClick={() => setOpen((value) => !value)} className={open ? 'tool-active' : ''}>
            Open fronts
          </button>
        </div>
      </div>
      <div className="model-canvas">
        <Canvas camera={{ position: [max * 1.5, max * 1.35, max * 2.1], fov: 42 }}>
          <SceneView
            module={module}
            solids={solids}
            hoveredPartIds={hoveredPartIds}
            reviewPartIds={highlightReview ? reviewPartIds : []}
            onSelect={onSelect}
            onHover={onHover}
            onScene={onScene}
            edges={edges}
            open={open}
            view={view}
          />
        </Canvas>
        <div className="camera-buttons">
          {(['iso', 'front', 'top', 'side'] as const).map((option) => (
            <button
              className={view === option ? 'tool-active' : ''}
              key={option}
              onClick={() => setView(option)}
            >
              {option[0].toUpperCase() + option.slice(1)}
            </button>
          ))}
        </div>
      </div>
    </section>
  )
}

function SceneView({
  module,
  solids,
  hoveredPartIds,
  reviewPartIds,
  onHover,
  onSelect,
  onScene,
  edges,
  open,
  view,
}: Props & { edges: boolean; open: boolean; view: View }) {
  const { camera, scene } = useThree()
  const materials = useMemo(
    () => new Map(module.materials.map((material) => [material.key, material])),
    [module.materials],
  )
  const hovered = new Set(hoveredPartIds)
  const target = useMemo(
    () =>
      [module.width_mm, module.height_mm, module.depth_mm].map((value) => value * 0.0005) as [
        number,
        number,
        number,
      ],
    [module.width_mm, module.height_mm, module.depth_mm],
  )
  useEffect(() => {
    onScene(scene)
  }, [onScene, scene])
  useEffect(() => {
    const distance = Math.max(module.width_mm, module.height_mm, module.depth_mm) * 0.0015
    const positions: Record<View, [number, number, number]> = {
      iso: [target[0] + distance, target[1] + distance * 0.8, target[2] + distance * 1.4],
      front: [target[0], target[1], target[2] + distance * 2],
      top: [target[0], target[1] + distance * 2, target[2] + 0.001],
      side: [target[0] + distance * 2, target[1], target[2]],
    }
    camera.up.set(0, view === 'top' ? 0 : 1, view === 'top' ? -1 : 0)
    camera.position.set(...positions[view])
    camera.lookAt(...target)
    camera.updateProjectionMatrix()
  }, [camera, module, target, view])
  return (
    <>
      <ambientLight intensity={1.4} />
      <directionalLight position={[4, 8, 6]} intensity={2} />
      <Grid args={[10, 10]} cellSize={0.1} sectionSize={1} fadeDistance={20} infiniteGrid />
      <OrbitControls key={view} makeDefault target={target} />
      <group>
        {solids
          .filter((solid) => !(open && solid.role === 'front'))
          .map((solid) => {
            const [x, y, z] = solid.position_mm.map((value) => value * 0.001)
            const [sx, sy, sz] = solid.size_mm.map((value) => value * 0.001)
            const material = materials.get(solid.material_key ?? '')
            const color = material
              ? materialColors[material.kind]
              : solid.role === 'front'
                ? '#cfcfcf'
                : '#e8dcc3'
            const uncertain = reviewPartIds.includes(solid.part_id)
            const isHovered = hovered.has(solid.part_id)
            return (
              <mesh
                key={solid.part_id}
                position={[x + sx / 2, y + sy / 2, z + sz / 2]}
                onClick={(event) => {
                  event.stopPropagation()
                  onSelect(solid)
                }}
                onPointerOver={(event) => {
                  event.stopPropagation()
                  onHover([solid.part_id])
                }}
                onPointerOut={() => onHover([])}
              >
                <boxGeometry args={[sx, sy, sz]} />
                <meshStandardMaterial
                  color={color}
                  emissive={isHovered ? '#4f7cff' : uncertain ? '#e09a2b' : '#000000'}
                  emissiveIntensity={isHovered ? 0.65 : uncertain ? 0.35 : 0}
                />
                <Edges visible={edges} />
              </mesh>
            )
          })}
      </group>
    </>
  )
}
