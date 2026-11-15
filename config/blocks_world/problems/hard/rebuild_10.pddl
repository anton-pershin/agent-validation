(define (problem rebuild-10)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear b) (clear e) (clear f) (hand-empty) (on d a) (on e d) (on f c) (on-table a) (on-table b) (on-table c))
  (:goal (and (on e a) (on-table a) (on-table b) (on-table c) (on-table d) (on-table f)))
)
