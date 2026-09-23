package main

import (
	"bytes"
	"encoding/binary"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"time"
)

const (
	EV_SYN             = 0x00
	SYN_REPORT         = 0x00
	EV_KEY             = 0x01
	BTN_TOUCH          = 0x14a // 330
	EV_ABS             = 0x03
	ABS_MT_SLOT        = 0x2f  // 47
	ABS_MT_TRACKING_ID = 0x39  // 57
	ABS_MT_POSITION_X  = 0x35  // 53
	ABS_MT_POSITION_Y  = 0x36  // 54
)

type InputEvent struct {
	TimeSec  int64
	TimeUsec int64
	Type     uint16
	Code     uint16
	Value    int32
}

func packEvent(evType uint16, code uint16, val int32) []byte {
	ev := InputEvent{
		TimeSec:  0,
		TimeUsec: 0,
		Type:     evType,
		Code:     code,
		Value:    val,
	}
	buf := new(bytes.Buffer)
	_ = binary.Write(buf, binary.LittleEndian, ev)
	return buf.Bytes()
}

type Injector struct {
	file   *os.File
	dev    string
	maxX   int
	maxY   int
	width  int
	height int
}

func NewInjector(dev string, maxX, maxY, width, height int) (*Injector, error) {
	f, err := os.OpenFile(dev, os.O_WRONLY, 0666)
	if err != nil {
		return nil, fmt.Errorf("failed to open %s: %w", dev, err)
	}
	return &Injector{
		file:   f,
		dev:    dev,
		maxX:   maxX,
		maxY:   maxY,
		width:  width,
		height: height,
	}, nil
}

func (inj *Injector) Close() {
	if inj.file != nil {
		inj.file.Close()
	}
}

func (inj *Injector) ToDev(x, y int) (int32, int32) {
	dx := int(float64(x) / float64(inj.width) * float64(inj.maxX))
	dy := int(float64(y) / float64(inj.height) * float64(inj.maxY))
	if dx < 0 {
		dx = 0
	} else if dx > inj.maxX {
		dx = inj.maxX
	}
	if dy < 0 {
		dy = 0
	} else if dy > inj.maxY {
		dy = inj.maxY
	}
	return int32(dx), int32(dy)
}

func (inj *Injector) send(events ...[]byte) {
	for _, ev := range events {
		_, _ = inj.file.Write(ev)
	}
}

func (inj *Injector) syn() {
	inj.send(packEvent(EV_SYN, SYN_REPORT, 0))
}

func (inj *Injector) tap(x, y int) {
	dx, dy := inj.ToDev(x, y)
	inj.send(
		packEvent(EV_ABS, ABS_MT_SLOT, 0),
		packEvent(EV_ABS, ABS_MT_TRACKING_ID, 0),
		packEvent(EV_ABS, ABS_MT_POSITION_X, dx),
		packEvent(EV_ABS, ABS_MT_POSITION_Y, dy),
		packEvent(EV_KEY, BTN_TOUCH, 1),
	)
	inj.syn()
	time.Sleep(30 * time.Millisecond)
	inj.send(
		packEvent(EV_ABS, ABS_MT_TRACKING_ID, -1),
		packEvent(EV_KEY, BTN_TOUCH, 0),
	)
	inj.syn()
}

func (inj *Injector) Curveball(strength float64, curveLeft bool, ballCx, ballCy int) {
	spinRadius := int(0.065 * math.Min(float64(inj.width), float64(inj.height)))
	targetY := int((0.55 - strength*0.28) * float64(inj.height))

	// Dismiss tray if open
	inj.tap(ballCx, int(0.45*float64(inj.height)))
	time.Sleep(50 * time.Millisecond)

	// Phase 1: Fast Natural Spin Charging (3.5 revolutions in ~0.9s)
	// 56 points at ~16ms = ~900ms
	nSpin := 56
	revs := 3.5
	dir := 1.0
	if curveLeft {
		dir = -1.0
	}

	type Point struct {
		x, y int32
	}
	spinPts := make([]Point, nSpin)
	for i := 0; i < nSpin; i++ {
		angle := (float64(i) / float64(nSpin)) * 2 * math.Pi * revs * dir
		px := ballCx + int(float64(spinRadius)*math.Cos(angle))
		py := ballCy + int(float64(spinRadius)*math.Sin(angle))
		dx, dy := inj.ToDev(px, py)
		spinPts[i] = Point{x: dx, y: dy}
	}

	// Phase 2: Snappy Bézier Flick Release (12 points at ~8ms = ~96ms)
	sx := spinPts[len(spinPts)-1].x
	sy := spinPts[len(spinPts)-1].y
	var ctrlX, ctrlY, endX, endY int32
	if curveLeft {
		// Counter-clockwise spin curves left in flight -> release towards upper-right
		ctrlX, ctrlY = inj.ToDev(int(0.82*float64(inj.width)), int(0.62*float64(inj.height)))
		endX, endY = inj.ToDev(int(0.78*float64(inj.width)), targetY)
	} else {
		// Clockwise spin curves right in flight -> release towards upper-left to land dead-center
		ctrlX, ctrlY = inj.ToDev(int(0.18*float64(inj.width)), int(0.62*float64(inj.height)))
		endX, endY = inj.ToDev(int(0.22*float64(inj.width)), targetY)
	}

	nArc := 12
	arcPts := make([]Point, nArc)
	for i := 0; i < nArc; i++ {
		t := float64(i) / float64(nArc-1)
		bx := (1-t)*(1-t)*float64(sx) + 2*(1-t)*t*float64(ctrlX) + t*t*float64(endX)
		by := (1-t)*(1-t)*float64(sy) + 2*(1-t)*t*float64(ctrlY) + t*t*float64(endY)
		arcPts[i] = Point{x: int32(bx), y: int32(by)}
	}

	// Execute Touch Down
	inj.send(
		packEvent(EV_ABS, ABS_MT_SLOT, 0),
		packEvent(EV_ABS, ABS_MT_TRACKING_ID, 1),
		packEvent(EV_ABS, ABS_MT_POSITION_X, spinPts[0].x),
		packEvent(EV_ABS, ABS_MT_POSITION_Y, spinPts[0].y),
		packEvent(EV_KEY, BTN_TOUCH, 1),
	)
	inj.syn()
	time.Sleep(16 * time.Millisecond)

	// Execute Spin
	for i := 1; i < len(spinPts); i++ {
		inj.send(
			packEvent(EV_ABS, ABS_MT_POSITION_X, spinPts[i].x),
			packEvent(EV_ABS, ABS_MT_POSITION_Y, spinPts[i].y),
		)
		inj.syn()
		time.Sleep(16 * time.Millisecond)
	}

	// Execute Throw Flick
	for _, pt := range arcPts {
		inj.send(
			packEvent(EV_ABS, ABS_MT_POSITION_X, pt.x),
			packEvent(EV_ABS, ABS_MT_POSITION_Y, pt.y),
		)
		inj.syn()
		time.Sleep(8 * time.Millisecond)
	}

	// Execute Touch Up
	inj.send(
		packEvent(EV_ABS, ABS_MT_TRACKING_ID, -1),
		packEvent(EV_KEY, BTN_TOUCH, 0),
	)
	inj.syn()
}

func (inj *Injector) Straight(strength float64, ballCx, ballCy int) {
	targetY := int((0.55 - strength*0.28) * float64(inj.height))

	// Dismiss tray if open
	inj.tap(ballCx, int(0.45*float64(inj.height)))
	time.Sleep(50 * time.Millisecond)

	sx, sy := inj.ToDev(ballCx, ballCy)
	ex, ey := inj.ToDev(ballCx, targetY)

	nPts := 10
	type Point struct {
		x, y int32
	}
	pts := make([]Point, nPts)
	for i := 0; i < nPts; i++ {
		t := float64(i) / float64(nPts-1)
		x := float64(sx) + t*float64(ex-sx)
		y := float64(sy) + t*float64(ey-sy)
		pts[i] = Point{x: int32(x), y: int32(y)}
	}

	// Touch Down
	inj.send(
		packEvent(EV_ABS, ABS_MT_SLOT, 0),
		packEvent(EV_ABS, ABS_MT_TRACKING_ID, 1),
		packEvent(EV_ABS, ABS_MT_POSITION_X, pts[0].x),
		packEvent(EV_ABS, ABS_MT_POSITION_Y, pts[0].y),
		packEvent(EV_KEY, BTN_TOUCH, 1),
	)
	inj.syn()
	time.Sleep(8 * time.Millisecond)

	// Swipe
	for i := 1; i < len(pts); i++ {
		inj.send(
			packEvent(EV_ABS, ABS_MT_POSITION_X, pts[i].x),
			packEvent(EV_ABS, ABS_MT_POSITION_Y, pts[i].y),
		)
		inj.syn()
		time.Sleep(8 * time.Millisecond)
	}

	// Touch Up
	inj.send(
		packEvent(EV_ABS, ABS_MT_TRACKING_ID, -1),
		packEvent(EV_KEY, BTN_TOUCH, 0),
	)
	inj.syn()
}

func main() {
	dev := flag.String("dev", "/dev/input/event2", "Touch device path")
	maxX := flag.Int("max-x", 32767, "Max X coordinate of digitizer")
	maxY := flag.Int("max-y", 32767, "Max Y coordinate of digitizer")
	width := flag.Int("width", 1080, "Screen pixel width")
	height := flag.Int("height", 2400, "Screen pixel height")
	action := flag.String("action", "curveball", "Action: curveball or straight")
	strength := flag.Float64("strength", 0.6, "Throw strength (0.0 - 1.0)")
	curveLeftFlag := flag.String("curve-dir", "random", "Curve direction: left, right, random")
	ballX := flag.Int("ball-x", -1, "Throw ball center X in screen pixels; defaults to center")
	ballY := flag.Int("ball-y", -1, "Throw ball center Y in screen pixels; defaults to 80% height")
	flag.Parse()

	inj, err := NewInjector(*dev, *maxX, *maxY, *width, *height)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
	defer inj.Close()

	if *ballX < 0 {
		*ballX = int(0.50 * float64(inj.width))
	}
	if *ballY < 0 {
		*ballY = int(0.80 * float64(inj.height))
	}

	if *action == "curveball" {
		curveLeft := rand.Float64() < 0.5
		if *curveLeftFlag == "left" {
			curveLeft = true
		} else if *curveLeftFlag == "right" {
			curveLeft = false
		}
		inj.Curveball(*strength, curveLeft, *ballX, *ballY)
	} else if *action == "straight" {
		inj.Straight(*strength, *ballX, *ballY)
	} else {
		fmt.Fprintf(os.Stderr, "Unknown action: %s\n", *action)
		os.Exit(1)
	}
}
