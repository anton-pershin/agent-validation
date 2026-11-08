(define (problem rebuild-04)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear a) (clear d) (clear f) (hand-empty) (on a b) (on d e) (on f c) (on-table b) (on-table c) (on-table e))
  (:goal (and (on a e) (on d c) (on e f) (on-table b) (on-table c) (on-table f)))
)
