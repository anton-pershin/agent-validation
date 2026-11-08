(define (problem rebuild-15)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear b) (clear e) (hand-empty) (on c a) (on d c) (on e d) (on-table a) (on-table b))
  (:goal (and (on a b) (on d c) (on e d) (on-table b) (on-table c)))
)
